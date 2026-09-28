"""The Prometheus exporter: format, business cache, failure tolerance and instrumentation."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, AsyncIterator, Optional

import httpx
import pytest
from fastapi import FastAPI
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families
from prometheus_client.process_collector import ProcessCollector

from src.__version__ import __version__
from src.application.common.dao import SubscriptionDao, TransactionDao
from src.application.dto import (
    GatewayStatsDto,
    PlanIncomeDto,
    PlanSubStatsDto,
    PromocodeStatisticsDto,
    ReferralStatisticsDto,
    SubscriptionStatsDto,
)
from src.application.use_cases.statistics.queries.plans import (
    GetPlanStatistics,
    PlansStatisticsDto,
)
from src.application.use_cases.statistics.queries.promocodes import GetPromocodeStatistics
from src.application.use_cases.statistics.queries.referrals import GetReferralStatistics
from src.application.use_cases.statistics.queries.subscriptions import GetSubscriptionStatistics
from src.application.use_cases.statistics.queries.transactions import (
    GetTransactionStatistics,
    TransactionStatisticsDto,
)
from src.application.use_cases.statistics.queries.users import (
    GetUsersStatistics,
    UsersStatisticsDto,
)
from src.core.config.metrics import MetricsConfig
from src.core.enums import PaymentGatewayType
from src.core.utils.time import datetime_now
from src.infrastructure.metrics import (
    REGISTRY,
    BusinessMetricsProvider,
    MetricsServer,
    WebhookOutcome,
    build_metrics_app,
    normalize_endpoint,
    observe_payment_webhook,
    register_business_collector,
    setup_http_metrics,
    unregister_business_collector,
)
from src.infrastructure.metrics.http import UNMATCHED, resolve_route_path
from src.infrastructure.metrics.payments import UNKNOWN_GATEWAY_LABEL

# --------------------------------------------------------------------------- fixtures / doubles

USERS = UsersStatisticsDto(
    total_users=1200,
    new_users_daily=7,
    new_users_weekly=42,
    new_users_monthly=180,
    users_with_subscription=800,
    users_without_subscription=400,
    users_with_trial=55,
    blocked_users=9,
    bot_blocked_users=31,
    user_conversion=41.5,
    trial_conversion=12.25,
)

SUBSCRIPTIONS = SubscriptionStatsDto(
    total=1000,
    total_active=700,
    total_disabled=60,
    total_limited=40,
    total_expired=200,
    active_trial=25,
    expiring_soon=33,
    total_unlimited=12,
    total_traffic=500,
    total_devices=450,
)

GATEWAY = GatewayStatsDto(
    gateway_type=PaymentGatewayType.CRYPTOPAY,
    total_income=Decimal("1500.50"),
    daily_income=Decimal("10.00"),
    weekly_income=Decimal("70.00"),
    monthly_income=Decimal("300.00"),
    last_month_income=Decimal("280.00"),
    paid_count=64,
    total_discounts=Decimal("25.00"),
    total_transactions=90,
    completed_transactions=64,
    free_transactions=3,
)

TRANSACTIONS = TransactionStatisticsDto(
    total_transactions=300,
    completed_transactions=250,
    free_transactions=11,
    popular_gateway=str(PaymentGatewayType.CRYPTOPAY),
    gateway_stats=[GATEWAY],
)

PLANS = PlansStatisticsDto(
    plans=[
        PlanSubStatsDto(
            plan_id=1,
            plan_name="Standard",
            total=500,
            total_active=430,
            total_disabled=20,
            total_limited=10,
            total_expired=40,
            expiring_soon=5,
            total_unlimited=0,
            total_traffic=1,
            total_devices=2,
            popular_duration=30,
        )
    ],
    income=[PlanIncomeDto(plan_id=1, currency="RUB", total_income=99000.0)],
)

PROMOCODES = PromocodeStatisticsDto(
    total_promocodes=20,
    active_promocodes=8,
    total_activations=140,
    activations_today=3,
    activations_week=19,
    activations_month=71,
    issued_days=900,
    issued_traffic=120,
    issued_devices=30,
    issued_subscriptions=6,
    issued_personal_discounts=4,
    issued_purchase_discounts=2,
)

REFERRALS = ReferralStatisticsDto(
    total_referrals=310,
    level_1_count=250,
    level_2_count=60,
    unique_referrers=88,
    total_rewards_issued=150,
    total_points_issued=4200,
    total_days_issued=610,
    top_referrer_referrals_count=17,
)


class _StubInteractor:
    """Mimics `Interactor.system`: awaiting it returns the canned DTO."""

    def __init__(self, value: Any) -> None:
        self._value = value
        self.calls = 0

    @property
    def system(self) -> Any:
        async def call(data: Any = None) -> Any:
            self.calls += 1
            return self._value

        return call


class _StubSubscriptionDao:
    def __init__(self, expiring: Optional[list[Any]] = None, conflicts: int = 0) -> None:
        self._expiring = expiring or []
        self._conflicts = conflicts

    async def get_expiring_current(self, until: Any) -> list[Any]:
        return [s for s in self._expiring if s.expire_at <= until]

    async def get_remna_id_conflicts(self) -> list[Any]:
        return [SimpleNamespace(user_remna_id=i) for i in range(self._conflicts)]


class _StubTransactionDao:
    def __init__(self, unfulfilled: int = 0) -> None:
        self._unfulfilled = unfulfilled

    async def count_unfulfilled(self) -> int:
        return self._unfulfilled


class _FakeContainer:
    """Minimal stand-in for `dishka.AsyncContainer` used by the business provider."""

    def __init__(self, mapping: dict[Any, Any], fail: bool = False) -> None:
        self.mapping = mapping
        self.fail = fail
        self.scopes_opened = 0

    def __call__(self, context: Any = None, scope: Any = None) -> Any:
        container = self

        @asynccontextmanager
        async def _scope() -> AsyncIterator[Any]:
            container.scopes_opened += 1
            if container.fail:
                raise RuntimeError("database is on fire")
            yield SimpleNamespace(get=container._get)

        return _scope()

    async def _get(self, key: Any) -> Any:
        return self.mapping[key]


def _mapping(
    *,
    expiring: Optional[list[Any]] = None,
    conflicts: int = 0,
    unfulfilled: int = 0,
) -> dict[Any, Any]:
    return {
        GetUsersStatistics: _StubInteractor(USERS),
        GetSubscriptionStatistics: _StubInteractor(SUBSCRIPTIONS),
        GetTransactionStatistics: _StubInteractor(TRANSACTIONS),
        GetPlanStatistics: _StubInteractor(PLANS),
        GetPromocodeStatistics: _StubInteractor(PROMOCODES),
        GetReferralStatistics: _StubInteractor(REFERRALS),
        SubscriptionDao: _StubSubscriptionDao(expiring=expiring, conflicts=conflicts),
        TransactionDao: _StubTransactionDao(unfulfilled=unfulfilled),
    }


@pytest.fixture
def business(request: pytest.FixtureRequest) -> Any:
    """A registered business collector backed by a fake container."""
    params = getattr(request, "param", {}) or {}
    container = _FakeContainer(
        _mapping(
            expiring=params.get("expiring"),
            conflicts=params.get("conflicts", 0),
            unfulfilled=params.get("unfulfilled", 0),
        ),
        fail=params.get("fail", False),
    )
    provider = BusinessMetricsProvider(
        container_factory=lambda: container,
        ttl=params.get("ttl", 60.0),
        timeout=params.get("timeout", 5.0),
    )
    collector = register_business_collector(
        provider, panel_overlap=params.get("panel_overlap", True)
    )
    try:
        yield SimpleNamespace(provider=provider, collector=collector, container=container)
    finally:
        unregister_business_collector(collector)


async def _call_asgi(app: Any, path: str = "/metrics") -> tuple[int, bytes, str]:
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:  # pragma: no cover - no request body
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [(b"accept", b"text/plain")],
    }
    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    content_type = next(v.decode() for k, v in start["headers"] if k == b"content-type")
    return int(start["status"]), body, content_type


# --------------------------------------------------------------------------- exposition format


@pytest.mark.asyncio
async def test_endpoint_returns_parsable_prometheus_text(business: Any) -> None:
    app = build_metrics_app("/metrics", before_scrape=business.provider.ensure_fresh)

    status, body, content_type = await _call_asgi(app)

    assert status == 200
    assert content_type.startswith("text/plain")
    families = {f.name: f for f in text_string_to_metric_families(body.decode())}
    # Parsing succeeded and both runtime and business families are present.
    assert "remnashop_uptime_seconds" in families
    assert "remnashop_build_info" in families
    assert "remnashop_users" in families


@pytest.mark.asyncio
async def test_unknown_path_is_404_and_root_is_a_liveness_probe() -> None:
    app = build_metrics_app("/metrics")

    assert (await _call_asgi(app, "/nope"))[0] == 404
    status, body, _ = await _call_asgi(app, "/")
    assert status == 200
    assert body == b"ok\n"


# --------------------------------------------------------------------------- business values


@pytest.mark.asyncio
async def test_business_gauges_match_the_interactor_results(business: Any) -> None:
    await business.provider.ensure_fresh()

    def sample(name: str, labels: Optional[dict[str, str]] = None) -> Optional[float]:
        return REGISTRY.get_sample_value(name, labels or {})

    assert sample("remnashop_users", {"state": "total"}) == USERS.total_users
    assert sample("remnashop_users", {"state": "blocked"}) == USERS.blocked_users
    assert sample("remnashop_users_new", {"period": "7d"}) == USERS.new_users_weekly

    assert sample("remnashop_subscriptions", {"status": "active"}) == SUBSCRIPTIONS.total_active
    assert sample("remnashop_subscriptions", {"status": "expired"}) == SUBSCRIPTIONS.total_expired
    assert sample("remnashop_subscriptions_all") == SUBSCRIPTIONS.total
    assert sample("remnashop_subscriptions_expiring_soon") == SUBSCRIPTIONS.expiring_soon

    assert sample("remnashop_transactions", {"state": "completed"}) == 250
    gateway = str(PaymentGatewayType.CRYPTOPAY)
    assert (
        sample("remnashop_gateway_transactions", {"gateway": gateway, "state": "paid"})
        == GATEWAY.paid_count
    )
    assert sample("remnashop_gateway_income", {"gateway": gateway, "period": "total"}) == float(
        GATEWAY.total_income
    )

    assert (
        sample(
            "remnashop_plan_subscriptions", {"plan_id": "1", "plan": "Standard", "state": "active"}
        )
        == 430
    )
    assert sample("remnashop_plan_income", {"plan_id": "1", "currency": "RUB"}) == 99000.0
    assert sample("remnashop_promocodes", {"state": "active"}) == PROMOCODES.active_promocodes
    assert sample("remnashop_referrals", {"level": "2"}) == REFERRALS.level_2_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "business",
    [
        {
            "expiring": None,
            "conflicts": 2,
            "unfulfilled": 5,
        }
    ],
    indirect=True,
)
async def test_integrity_gauges_expose_unfulfilled_payments_and_remna_id_conflicts(
    business: Any,
) -> None:
    await business.provider.ensure_fresh()

    assert REGISTRY.get_sample_value("remnashop_payments_unfulfilled") == 5
    assert REGISTRY.get_sample_value("remnashop_remna_id_conflicts") == 2


@pytest.mark.asyncio
async def test_expiring_windows_are_bucketed_from_one_query() -> None:
    now = datetime_now()
    expiring = [
        SimpleNamespace(expire_at=now + timedelta(hours=2)),
        SimpleNamespace(expire_at=now + timedelta(hours=20)),
        SimpleNamespace(expire_at=now + timedelta(days=2)),
        SimpleNamespace(expire_at=now + timedelta(days=6)),
        SimpleNamespace(expire_at=now + timedelta(days=30)),
    ]
    container = _FakeContainer(_mapping(expiring=expiring))
    provider = BusinessMetricsProvider(lambda: container, ttl=60.0, timeout=5.0)
    collector = register_business_collector(provider)
    try:
        await provider.ensure_fresh()
        assert REGISTRY.get_sample_value("remnashop_subscriptions_expiring", {"window": "24h"}) == 2
        assert REGISTRY.get_sample_value("remnashop_subscriptions_expiring", {"window": "3d"}) == 3
        assert REGISTRY.get_sample_value("remnashop_subscriptions_expiring", {"window": "7d"}) == 4
    finally:
        unregister_business_collector(collector)


# --------------------------------------------------------------------------- caching


@pytest.mark.asyncio
async def test_cache_is_not_recomputed_within_the_ttl(business: Any) -> None:
    for _ in range(5):
        await business.provider.ensure_fresh()

    assert business.container.scopes_opened == 1
    assert business.container.mapping[GetUsersStatistics].calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("business", [{"ttl": 0.0}], indirect=True)
async def test_cache_is_recomputed_once_the_ttl_elapsed(business: Any) -> None:
    await business.provider.ensure_fresh()
    await business.provider.ensure_fresh()

    assert business.container.scopes_opened == 2


# --------------------------------------------------------------------------- failure tolerance


@pytest.mark.asyncio
@pytest.mark.parametrize("business", [{"fail": True}], indirect=True)
async def test_collection_failure_keeps_the_endpoint_healthy_and_counts_the_error(
    business: Any,
) -> None:
    before = REGISTRY.get_sample_value("remnashop_business_collection_errors_total") or 0.0
    app = build_metrics_app("/metrics", before_scrape=business.provider.ensure_fresh)

    status, body, _ = await _call_asgi(app)

    assert status == 200
    after = REGISTRY.get_sample_value("remnashop_business_collection_errors_total")
    assert after == before + 1
    text = body.decode()
    # Nothing business related is exported, but the process metrics still are.
    assert "remnashop_users" not in text
    assert "remnashop_uptime_seconds" in text
    assert list(text_string_to_metric_families(text))


@pytest.mark.asyncio
@pytest.mark.parametrize("business", [{"fail": True}], indirect=True)
async def test_a_failing_collection_is_not_retried_on_every_scrape(business: Any) -> None:
    await business.provider.ensure_fresh()
    await business.provider.ensure_fresh()

    assert business.container.scopes_opened == 1


# --------------------------------------------------------------------------- instrumentation


def test_every_instrumented_subsystem_registers_its_metrics() -> None:
    exported = generate_latest(REGISTRY).decode()
    names = {family.name for family in text_string_to_metric_families(exported)}

    # `ProcessCollector` only produces samples on platforms with /proc, so its presence in the
    # registry is asserted instead of its output.
    assert any(
        isinstance(collector, ProcessCollector)
        for collector in REGISTRY._collector_to_names  # noqa: SLF001
    )

    expected = {
        # runtime
        "python_gc_collections",
        "python_info",
        "remnashop_uptime_seconds",
        "remnashop_build_info",
        # HTTP
        "remnashop_http_requests",
        "remnashop_http_request_duration_seconds",
        # database
        "remnashop_db_pool_wait_seconds",
        "remnashop_db_pool_events",
        "remnashop_db_errors",
        "remnashop_db_query_duration_seconds",
        # taskiq
        "remnashop_taskiq_tasks_executed",
        "remnashop_taskiq_task_duration_seconds",
        # aiogram
        "remnashop_telegram_updates",
        "remnashop_telegram_update_duration_seconds",
        # Remnawave
        "remnashop_remnawave_requests",
        "remnashop_remnawave_request_duration_seconds",
        # payment webhooks
        "remnashop_payment_webhooks",
    }
    assert expected <= names


def test_build_info_carries_the_application_version() -> None:
    exported = generate_latest(REGISTRY).decode()
    assert f'version="{__version__}"' in exported


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/users/12345", "/users/{id}"),
        ("/api/users/12345/actions/enable", "/users/{id}/actions/enable"),
        ("/api/users/by-username/rs_9999", "/users/by-username/{username}"),
        ("/api/hwid/devices/777", "/hwid/devices/{id}"),
        ("/api/users/stream", "/users/stream"),
        (
            "/api/users/11111111-2222-3333-4444-555555555555",
            "/users/{id}",
        ),
    ],
)
def test_remnawave_endpoints_are_templated(path: str, expected: str) -> None:
    assert normalize_endpoint(path) == expected


def test_payment_webhook_gateway_label_cannot_be_forged() -> None:
    observe_payment_webhook("../../evil", WebhookOutcome.REJECTED)

    assert (
        REGISTRY.get_sample_value(
            "remnashop_payment_webhooks_total",
            {"gateway": UNKNOWN_GATEWAY_LABEL, "outcome": "rejected"},
        )
        == 1
    )


def test_http_route_paths_use_templates_and_collapse_unmatched_urls() -> None:
    app = FastAPI()

    @app.get("/api/v1/thing/{thing_id}")
    async def _thing(thing_id: int) -> dict:  # pragma: no cover - never executed
        return {}

    matched = {"type": "http", "method": "GET", "path": "/api/v1/thing/42", "root_path": ""}
    unmatched = {"type": "http", "method": "GET", "path": "/nope", "root_path": ""}

    assert resolve_route_path(app.router.routes, matched) == "/api/v1/thing/{thing_id}"
    assert resolve_route_path(app.router.routes, unmatched) == UNMATCHED


@pytest.mark.asyncio
async def test_http_middleware_counts_requests_by_route_template() -> None:
    app = FastAPI()

    @app.get("/api/v1/thing/{thing_id}")
    async def _thing(thing_id: int) -> dict:
        return {"id": thing_id}

    setup_http_metrics(app)

    label = {"method": "GET", "path": "/api/v1/thing/{thing_id}", "status": "200"}
    before = REGISTRY.get_sample_value("remnashop_http_requests_total", label) or 0.0

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/thing/7")

    assert response.status_code == 200
    assert REGISTRY.get_sample_value("remnashop_http_requests_total", label) == before + 1


# --------------------------------------------------------------------------- the second server


@pytest.mark.asyncio
async def test_metrics_server_listens_on_its_own_port_and_stops_cleanly() -> None:
    config = MetricsConfig(enabled=True, host="127.0.0.1", port=0, path="/metrics")
    server = MetricsServer(config)
    await server.start()
    try:
        assert server.started
        port = server.port
        assert port
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for _ in range(50):
                try:
                    response = await client.get("/metrics")
                    break
                except httpx.ConnectError:  # pragma: no cover - server still binding
                    await asyncio.sleep(0.02)
            else:  # pragma: no cover - the server never came up
                pytest.fail("metrics server did not accept connections")
        assert response.status_code == 200
        assert list(text_string_to_metric_families(response.text))
    finally:
        await server.stop()

    assert not server.started


@pytest.mark.asyncio
async def test_a_taken_port_disables_the_exporter_instead_of_failing_the_process() -> None:
    first = MetricsServer(MetricsConfig(enabled=True, host="127.0.0.1", port=0))
    await first.start()
    try:
        second = MetricsServer(MetricsConfig(enabled=True, host="127.0.0.1", port=first.port or 0))
        await second.start()
        assert not second.started
        await second.stop()
    finally:
        await first.stop()


# --------------------------------------------------------- series duplicating the panel's own


@pytest.mark.asyncio
@pytest.mark.parametrize("business", [{"panel_overlap": False}], indirect=True)
async def test_panel_overlapping_series_are_hidden_by_default(business: Any) -> None:
    """Remnawave already exports users_status / users_total; ours stay in the code but off."""
    await business.provider.ensure_fresh()

    assert REGISTRY.get_sample_value("remnashop_subscriptions", {"status": "expired"}) is None
    assert REGISTRY.get_sample_value("remnashop_users", {"state": "total"}) is None
    # Bot-only states keep being exported: the panel has no idea about them.
    assert REGISTRY.get_sample_value("remnashop_users", {"state": "blocked"}) is not None
    assert REGISTRY.get_sample_value("remnashop_subscriptions_all") is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("business", [{"panel_overlap": True}], indirect=True)
async def test_panel_overlapping_series_can_be_turned_on(business: Any) -> None:
    await business.provider.ensure_fresh()

    assert REGISTRY.get_sample_value("remnashop_subscriptions", {"status": "expired"}) == (
        SUBSCRIPTIONS.total_expired
    )
    assert REGISTRY.get_sample_value("remnashop_users", {"state": "total"}) == USERS.total_users
