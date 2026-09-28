# ruff: noqa: PLC0415
"""The exporter against the real container: DI resolves, and the numbers are the bot's own."""

from typing import Any

import pytest

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]


def _business_provider(app: Any, ttl: float = 60.0) -> Any:
    from dishka.integrations.aiogram import AiogramMiddlewareData

    from src.infrastructure.metrics import BusinessMetricsProvider

    return BusinessMetricsProvider(
        container_factory=lambda: app.container,
        ttl=ttl,
        timeout=30.0,
        context={AiogramMiddlewareData: AiogramMiddlewareData({})},
    )


async def test_business_metrics_collect_against_the_real_container(app: Any) -> None:
    """Every statistics interactor must be resolvable outside of a Telegram update."""
    from src.infrastructure.metrics import REGISTRY

    provider = _business_provider(app)
    before = REGISTRY.get_sample_value("remnashop_business_collection_errors_total") or 0.0

    await provider.ensure_fresh()

    assert REGISTRY.get_sample_value("remnashop_business_collection_errors_total") == before
    snapshot = provider.snapshot
    assert snapshot is not None
    assert snapshot.users.total_users > 0


async def test_exported_numbers_equal_the_interactor_results(app: Any) -> None:
    from dishka import Scope

    from src.application.use_cases.statistics.queries.subscriptions import (
        GetSubscriptionStatistics,
    )
    from src.application.use_cases.statistics.queries.users import GetUsersStatistics
    from src.infrastructure.metrics import (
        REGISTRY,
        register_business_collector,
        unregister_business_collector,
    )

    async with app.container(scope=Scope.REQUEST) as request:
        users = await (await request.get(GetUsersStatistics)).system()
        subscriptions = await (await request.get(GetSubscriptionStatistics)).system()

    provider = _business_provider(app)
    # panel_overlap: also export the two series that Remnawave itself reports, so this test
    # checks every gauge the exporter can produce.
    collector = register_business_collector(provider, panel_overlap=True)
    try:
        await provider.ensure_fresh()
        assert REGISTRY.get_sample_value("remnashop_users", {"state": "total"}) == (
            users.total_users
        )
        assert REGISTRY.get_sample_value("remnashop_users", {"state": "with_subscription"}) == (
            users.users_with_subscription
        )
        assert REGISTRY.get_sample_value("remnashop_subscriptions", {"status": "active"}) == (
            subscriptions.total_active
        )
        assert REGISTRY.get_sample_value("remnashop_subscriptions", {"status": "expired"}) == (
            subscriptions.total_expired
        )
        assert REGISTRY.get_sample_value("remnashop_subscriptions_all") == subscriptions.total
    finally:
        unregister_business_collector(collector)


async def test_the_exporter_runs_on_its_own_port_beside_the_webhook_app(app: Any) -> None:
    import httpx
    from dishka.integrations.aiogram import AiogramMiddlewareData
    from prometheus_client.parser import text_string_to_metric_families

    from src.core.config.metrics import MetricsConfig
    from src.infrastructure.metrics import MetricsRuntime

    config = app.config.model_copy(
        update={"metrics": MetricsConfig(enabled=True, host="127.0.0.1", port=0)}
    )
    runtime = MetricsRuntime(
        config=config,
        container_factory=lambda: app.container,
        role="app",
        business_context={AiogramMiddlewareData: AiogramMiddlewareData({})},
    )
    await runtime.start()
    try:
        server = runtime._server  # noqa: SLF001
        assert server is not None and server.started
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{server.port}") as client:
            response = await client.get("/metrics")
        assert response.status_code == 200
        names = {f.name for f in text_string_to_metric_families(response.text)}
        assert {"remnashop_users", "remnashop_db_pool_connections"} <= names
    finally:
        await runtime.stop()

    # The webhook application's own port is untouched by the exporter's lifecycle.
    assert app.fastapi.state.dishka_container is app.container
