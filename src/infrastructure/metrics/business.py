"""Cached business gauges, read through the very interactors the admin dashboard uses.

Every number here comes from `src.application.use_cases.statistics` (or from the DAO method the
bot itself calls), so a Grafana panel and the bot's statistics screen can never disagree.

Collection is asynchronous and cached: `BusinessMetricsProvider.ensure_fresh()` is awaited by
the exporter before rendering and does real work at most once per TTL. A failed or timed-out
pass only bumps `remnashop_business_collection_errors_total` and keeps the previous snapshot
(or no business series at all, when nothing has been collected yet) — the endpoint always
answers 200.
"""

import asyncio
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable, Optional

from dishka import AsyncContainer, Scope
from loguru import logger
from prometheus_client.metrics_core import GaugeMetricFamily, Metric

from src.application.common.dao import SubscriptionDao, TransactionDao
from src.application.common.dao.subscription import RemnaIdConflictDto
from src.application.dto import (
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
from src.core.utils.time import datetime_now

from .registry import (
    BUSINESS_COLLECTION_DURATION,
    BUSINESS_COLLECTION_ERRORS,
    BUSINESS_COLLECTIONS,
    BUSINESS_LAST_SUCCESS,
    REGISTRY,
)

# Windows used by `remnashop_subscriptions_expiring`; the 7d bucket shares its source with the
# expiry-reminder task, so the graph matches the notifications that actually go out.
EXPIRY_WINDOWS: tuple[tuple[str, timedelta], ...] = (
    ("24h", timedelta(hours=24)),
    ("3d", timedelta(days=3)),
    ("7d", timedelta(days=7)),
)


@dataclass(frozen=True)
class BusinessSnapshot:
    users: UsersStatisticsDto
    subscriptions: SubscriptionStatsDto
    transactions: TransactionStatisticsDto
    plans: PlansStatisticsDto
    promocodes: PromocodeStatisticsDto
    referrals: ReferralStatisticsDto
    expiring: dict[str, int]
    unfulfilled_payments: int
    remna_id_conflicts: list[RemnaIdConflictDto] = field(default_factory=list)


class BusinessMetricsProvider:
    """Owns the cached snapshot and refreshes it at most once per `ttl` seconds."""

    def __init__(
        self,
        container_factory: Callable[[], AsyncContainer],
        ttl: float,
        timeout: float,
        context: Optional[dict] = None,
    ) -> None:
        self._container_factory = container_factory
        self._ttl = ttl
        self._timeout = timeout
        self._context = context
        self._lock = asyncio.Lock()
        self._snapshot: Optional[BusinessSnapshot] = None
        self._last_attempt: Optional[float] = None

    @property
    def snapshot(self) -> Optional[BusinessSnapshot]:
        return self._snapshot

    def _is_fresh(self) -> bool:
        return (
            self._last_attempt is not None and (time.monotonic() - self._last_attempt) < self._ttl
        )

    async def ensure_fresh(self) -> None:
        """Refresh the snapshot when the TTL elapsed. Never raises."""
        if self._is_fresh():
            return

        async with self._lock:
            if self._is_fresh():
                return
            started = time.monotonic()
            try:
                self._snapshot = await asyncio.wait_for(self._collect(), timeout=self._timeout)
            except Exception as e:
                BUSINESS_COLLECTION_ERRORS.inc()
                logger.warning(f"Failed to collect business metrics: '{e}'")
            else:
                duration = time.monotonic() - started
                BUSINESS_COLLECTIONS.inc()
                BUSINESS_COLLECTION_DURATION.set(duration)
                BUSINESS_LAST_SUCCESS.set(time.time())
            finally:
                # Set even on failure: a broken query must not be retried on every scrape.
                self._last_attempt = time.monotonic()

    async def _collect(self) -> BusinessSnapshot:
        container = self._container_factory()
        async with container(self._context, scope=Scope.REQUEST) as request:
            users = await (await request.get(GetUsersStatistics)).system()
            subscriptions = await (await request.get(GetSubscriptionStatistics)).system()
            transactions = await (await request.get(GetTransactionStatistics)).system()
            plans = await (await request.get(GetPlanStatistics)).system()
            promocodes = await (await request.get(GetPromocodeStatistics)).system()
            referrals = await (await request.get(GetReferralStatistics)).system()

            subscription_dao = await request.get(SubscriptionDao)
            transaction_dao = await request.get(TransactionDao)
            conflicts = await subscription_dao.get_remna_id_conflicts()
            unfulfilled = await transaction_dao.count_unfulfilled()
            expiring = await self._collect_expiring(subscription_dao)

        return BusinessSnapshot(
            users=users,
            subscriptions=subscriptions,
            transactions=transactions,
            plans=plans,
            promocodes=promocodes,
            referrals=referrals,
            expiring=expiring,
            unfulfilled_payments=unfulfilled,
            remna_id_conflicts=conflicts,
        )

    @staticmethod
    async def _collect_expiring(subscription_dao: SubscriptionDao) -> dict[str, int]:
        """One query for the widest window, bucketed in memory: all three series agree."""
        now = datetime_now()
        widest = max(delta for _, delta in EXPIRY_WINDOWS)
        subscriptions = await subscription_dao.get_expiring_current(now + widest)
        buckets = {name: 0 for name, _ in EXPIRY_WINDOWS}
        for subscription in subscriptions:
            expire_at = subscription.expire_at
            if expire_at is None:
                continue
            for name, delta in EXPIRY_WINDOWS:
                if expire_at <= now + delta:
                    buckets[name] += 1
        return buckets


def _gauge(name: str, documentation: str, value: float) -> GaugeMetricFamily:
    return GaugeMetricFamily(name, documentation, value=value)


def _labeled(name: str, documentation: str, labels: tuple[str, ...]) -> GaugeMetricFamily:
    return GaugeMetricFamily(name, documentation, labels=list(labels))


class BusinessCollector:
    """Renders the cached snapshot; yields nothing while no snapshot exists."""

    def __init__(self, provider: BusinessMetricsProvider, panel_overlap: bool = False) -> None:
        self._provider = provider
        # Series that duplicate what the Remnawave panel already exports (see MetricsConfig).
        self._panel_overlap = panel_overlap

    def describe(self) -> list[Metric]:
        return []

    def collect(self) -> Iterator[Metric]:
        snapshot = self._provider.snapshot
        if snapshot is None:
            return
        yield from self._subscriptions(snapshot)
        yield from self._users(snapshot)
        yield from self._transactions(snapshot)
        yield from self._plans(snapshot)
        yield from self._promocodes(snapshot)
        yield from self._referrals(snapshot)
        yield from self._integrity(snapshot)

    def _subscriptions(self, snapshot: BusinessSnapshot) -> Iterator[Metric]:
        stats = snapshot.subscriptions
        if self._panel_overlap:
            # Panel counterpart: users_status (counts every panel user, not only bot ones).
            by_status = _labeled(
                "remnashop_subscriptions",
                "Current subscriptions by status (DELETED excluded)",
                ("status",),
            )
            by_status.add_metric(["active"], stats.total_active)
            by_status.add_metric(["disabled"], stats.total_disabled)
            by_status.add_metric(["limited"], stats.total_limited)
            by_status.add_metric(["expired"], stats.total_expired)
            yield by_status

        yield _gauge(
            "remnashop_subscriptions_all",
            "Current subscriptions of any status (DELETED excluded)",
            stats.total,
        )
        yield _gauge(
            "remnashop_subscriptions_trial_active",
            "Active trial subscriptions",
            stats.active_trial,
        )
        yield _gauge(
            "remnashop_subscriptions_unlimited",
            "Active subscriptions without traffic and device limits",
            stats.total_unlimited,
        )
        yield _gauge(
            "remnashop_subscriptions_expiring_soon",
            "Active subscriptions expiring within 7 days, exactly as the bot's statistics "
            "screen counts them",
            stats.expiring_soon,
        )

        expiring = _labeled(
            "remnashop_subscriptions_expiring",
            "Active current subscriptions of reachable users expiring within the window "
            "(same source as the expiry reminder task)",
            ("window",),
        )
        for name, _ in EXPIRY_WINDOWS:
            expiring.add_metric([name], snapshot.expiring.get(name, 0))
        yield expiring

    def _users(self, snapshot: BusinessSnapshot) -> Iterator[Metric]:
        stats = snapshot.users
        by_state = _labeled("remnashop_users", "Bot users by state", ("state",))
        if self._panel_overlap:
            # Panel counterpart: users_total.
            by_state.add_metric(["total"], stats.total_users)
        by_state.add_metric(["with_subscription"], stats.users_with_subscription)
        by_state.add_metric(["without_subscription"], stats.users_without_subscription)
        by_state.add_metric(["with_trial"], stats.users_with_trial)
        by_state.add_metric(["blocked"], stats.blocked_users)
        by_state.add_metric(["bot_blocked"], stats.bot_blocked_users)
        yield by_state

        new_users = _labeled(
            "remnashop_users_new",
            "Users registered within the period",
            ("period",),
        )
        new_users.add_metric(["1d"], stats.new_users_daily)
        new_users.add_metric(["7d"], stats.new_users_weekly)
        new_users.add_metric(["30d"], stats.new_users_monthly)
        yield new_users

        conversion = _labeled(
            "remnashop_conversion_percent",
            "Conversion percentages shown on the statistics screen",
            ("kind",),
        )
        conversion.add_metric(["paying"], stats.user_conversion)
        conversion.add_metric(["trial"], stats.trial_conversion)
        yield conversion

    @staticmethod
    def _transactions(snapshot: BusinessSnapshot) -> Iterator[Metric]:
        stats = snapshot.transactions
        by_state = _labeled("remnashop_transactions", "Transactions by state", ("state",))
        by_state.add_metric(["total"], stats.total_transactions)
        by_state.add_metric(["completed"], stats.completed_transactions)
        by_state.add_metric(["free"], stats.free_transactions)
        yield by_state

        gateway_transactions = _labeled(
            "remnashop_gateway_transactions",
            "Transactions by payment gateway and state",
            ("gateway", "state"),
        )
        gateway_income = _labeled(
            "remnashop_gateway_income",
            "Income by payment gateway and period, in the gateway's own currency",
            ("gateway", "period"),
        )
        gateway_discounts = _labeled(
            "remnashop_gateway_discounts",
            "Discounts granted on payments of the gateway",
            ("gateway",),
        )
        for gateway in stats.gateway_stats:
            name = str(gateway.gateway_type)
            gateway_transactions.add_metric([name, "total"], gateway.total_transactions)
            gateway_transactions.add_metric([name, "completed"], gateway.completed_transactions)
            gateway_transactions.add_metric([name, "free"], gateway.free_transactions)
            gateway_transactions.add_metric([name, "paid"], gateway.paid_count)
            gateway_income.add_metric([name, "total"], float(gateway.total_income))
            gateway_income.add_metric([name, "1d"], float(gateway.daily_income))
            gateway_income.add_metric([name, "7d"], float(gateway.weekly_income))
            gateway_income.add_metric([name, "30d"], float(gateway.monthly_income))
            gateway_income.add_metric([name, "previous_month"], float(gateway.last_month_income))
            gateway_discounts.add_metric([name], float(gateway.total_discounts))
        yield gateway_transactions
        yield gateway_income
        yield gateway_discounts

    @staticmethod
    def _plans(snapshot: BusinessSnapshot) -> Iterator[Metric]:
        plan_subscriptions = _labeled(
            "remnashop_plan_subscriptions",
            "Subscriptions by plan and state",
            ("plan_id", "plan", "state"),
        )
        for plan in snapshot.plans.plans:
            plan_id = str(plan.plan_id)
            plan_subscriptions.add_metric([plan_id, plan.plan_name, "total"], plan.total)
            plan_subscriptions.add_metric([plan_id, plan.plan_name, "active"], plan.total_active)
            plan_subscriptions.add_metric(
                [plan_id, plan.plan_name, "disabled"], plan.total_disabled
            )
            plan_subscriptions.add_metric([plan_id, plan.plan_name, "limited"], plan.total_limited)
            plan_subscriptions.add_metric([plan_id, plan.plan_name, "expired"], plan.total_expired)
        yield plan_subscriptions

        plan_income = _labeled(
            "remnashop_plan_income",
            "Income by plan and currency",
            ("plan_id", "currency"),
        )
        for income in snapshot.plans.income:
            plan_income.add_metric([str(income.plan_id), income.currency], income.total_income)
        yield plan_income

    @staticmethod
    def _promocodes(snapshot: BusinessSnapshot) -> Iterator[Metric]:
        stats = snapshot.promocodes
        by_state = _labeled("remnashop_promocodes", "Promocodes by state", ("state",))
        by_state.add_metric(["total"], stats.total_promocodes)
        by_state.add_metric(["active"], stats.active_promocodes)
        yield by_state

        activations = _labeled(
            "remnashop_promocode_activations",
            "Promocode activations by period",
            ("period",),
        )
        activations.add_metric(["total"], stats.total_activations)
        activations.add_metric(["1d"], stats.activations_today)
        activations.add_metric(["7d"], stats.activations_week)
        activations.add_metric(["30d"], stats.activations_month)
        yield activations

        issued = _labeled(
            "remnashop_promocode_issued",
            "Rewards issued through promocodes",
            ("reward",),
        )
        issued.add_metric(["days"], stats.issued_days)
        issued.add_metric(["traffic"], stats.issued_traffic)
        issued.add_metric(["devices"], stats.issued_devices)
        issued.add_metric(["subscriptions"], stats.issued_subscriptions)
        issued.add_metric(["personal_discounts"], stats.issued_personal_discounts)
        issued.add_metric(["purchase_discounts"], stats.issued_purchase_discounts)
        yield issued

    @staticmethod
    def _referrals(snapshot: BusinessSnapshot) -> Iterator[Metric]:
        stats = snapshot.referrals
        by_level = _labeled("remnashop_referrals", "Referrals by level", ("level",))
        by_level.add_metric(["total"], stats.total_referrals)
        by_level.add_metric(["1"], stats.level_1_count)
        by_level.add_metric(["2"], stats.level_2_count)
        yield by_level

        yield _gauge(
            "remnashop_referral_referrers",
            "Users who invited at least one referral",
            stats.unique_referrers,
        )

        rewards = _labeled(
            "remnashop_referral_rewards_issued",
            "Referral rewards issued",
            ("kind",),
        )
        rewards.add_metric(["total"], stats.total_rewards_issued)
        rewards.add_metric(["points"], stats.total_points_issued)
        rewards.add_metric(["days"], stats.total_days_issued)
        yield rewards

    @staticmethod
    def _integrity(snapshot: BusinessSnapshot) -> Iterator[Metric]:
        yield _gauge(
            "remnashop_payments_unfulfilled",
            "Paid (COMPLETED) transactions whose subscription was never granted "
            "(fulfilled_at IS NULL)",
            snapshot.unfulfilled_payments,
        )
        yield _gauge(
            "remnashop_remna_id_conflicts",
            "Remnawave user IDs bound to the current subscriptions of several bot users",
            len(snapshot.remna_id_conflicts),
        )


def register_business_collector(
    provider: BusinessMetricsProvider, panel_overlap: bool = False
) -> BusinessCollector:
    collector = BusinessCollector(provider, panel_overlap=panel_overlap)
    REGISTRY.register(collector)  # type: ignore[arg-type]
    return collector


def unregister_business_collector(collector: BusinessCollector) -> None:
    try:
        REGISTRY.unregister(collector)  # type: ignore[arg-type]
    except KeyError:  # pragma: no cover - defensive, unregister is idempotent in practice
        pass
