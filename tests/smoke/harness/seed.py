# ruff: noqa: PLC0415
"""Deterministic seed data for smoke tests (inserted through the ORM models of the project)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from adaptix import Retort
from dishka import Scope
from remnapy.enums import TrafficLimitStrategy
from sqlalchemy.ext.asyncio import AsyncSession

from .app import SmokeApp
from .fake_panel import INTERNAL_SQUAD_UUID

ADMIN_ID = 200_000_001
SUBSCRIBER_ID = 300_000_001
NEWBIE_ID = 400_000_001


@dataclass
class SeedIds:
    owner_tg: int
    admin_tg: int
    subscriber_tg: int
    newbie_tg: int
    owner_id: int
    admin_id: int
    subscriber_id: int
    newbie_id: int
    plan_id: int
    trial_plan_id: int
    subscription_id: int
    subscriber_remna_id: int


async def seed(app: SmokeApp) -> SeedIds:
    from src.application.dto import PlanSnapshotDto
    from src.application.use_cases.gateways.commands.payment import CreateDefaultPaymentGateway
    from src.application.use_cases.settings.commands.defaults import CreateDefaultSettings
    from src.core.constants import REMNASHOP_PREFIX
    from src.core.enums import (
        AuthType,
        Currency,
        Locale,
        PlanAvailability,
        PlanType,
        Role,
        SubscriptionStatus,
    )
    from src.infrastructure.database.models import (
        Plan,
        PlanDuration,
        PlanPrice,
        Subscription,
        User,
    )

    owner_tg = app.config.bot.owner_id

    async with app.container(scope=Scope.REQUEST) as request:
        await (await request.get(CreateDefaultPaymentGateway)).system()
        await (await request.get(CreateDefaultSettings)).system()

    async with app.container(scope=Scope.REQUEST) as request:
        session = await request.get(AsyncSession)
        retort = await request.get(Retort)

        def user(tg: int, name: str, role: Role) -> User:
            return User(
                telegram_id=tg,
                auth_type=AuthType.TELEGRAM,
                username=name.lower(),
                referral_code=f"REF{tg}",
                name=name,
                role=role,
                language=Locale.RU,
                personal_discount=0,
                purchase_discount=0,
                points=0,
                is_email_verified=False,
                is_blocked=False,
                is_bot_blocked=False,
                is_rules_accepted=True,
                is_trial_available=True,
            )

        def durations(*days_prices: tuple[int, int]) -> list[PlanDuration]:
            return [
                PlanDuration(
                    days=days,
                    order_index=i,
                    prices=[PlanPrice(currency=c, price=Decimal(price)) for c in Currency],
                )
                for i, (days, price) in enumerate(days_prices)
            ]

        squads = [UUID(INTERNAL_SQUAD_UUID)]
        plan = Plan(
            public_code="SMOKESTD",
            name="Smoke Standard",
            description="Standard smoke plan",
            tag="STD",
            type=PlanType.BOTH,
            availability=PlanAvailability.ALL,
            traffic_limit_strategy=TrafficLimitStrategy.NO_RESET,
            traffic_limit=100,
            device_limit=3,
            allowed_telegram_ids=[],
            allowed_emails=[],
            internal_squads=squads,
            external_squad=None,
            order_index=1,
            is_active=True,
            is_trial=False,
            durations=durations((30, 100), (90, 250)),
        )
        trial = Plan(
            public_code="SMOKETRL",
            name="Smoke Trial",
            description="Free trial",
            tag="TRIAL",
            type=PlanType.BOTH,
            availability=PlanAvailability.ALL,
            traffic_limit_strategy=TrafficLimitStrategy.NO_RESET,
            traffic_limit=10,
            device_limit=1,
            allowed_telegram_ids=[],
            allowed_emails=[],
            internal_squads=squads,
            external_squad=None,
            order_index=0,
            is_active=True,
            is_trial=True,
            durations=durations((3, 0)),
        )
        owner = user(owner_tg, "Owner", Role.OWNER)
        admin = user(ADMIN_ID, "Admin", Role.ADMIN)
        subscriber = user(SUBSCRIBER_ID, "Subscriber", Role.USER)
        newbie = user(NEWBIE_ID, "Newbie", Role.USER)
        session.add_all([plan, trial, owner, admin, subscriber, newbie])
        await session.flush()

        panel_user = app.panel.add_user(
            username=f"{REMNASHOP_PREFIX}{SUBSCRIBER_ID}", telegram_id=SUBSCRIBER_ID
        )
        snapshot = PlanSnapshotDto(
            id=plan.id,
            name=plan.name,
            tag=plan.tag,
            type=plan.type,
            traffic_limit=100,
            device_limit=3,
            duration=30,
            internal_squads=squads,
        )
        subscription = Subscription(
            user_remna_id=panel_user["id"],
            user_id=subscriber.id,
            status=SubscriptionStatus.ACTIVE,
            is_trial=False,
            traffic_limit=100,
            device_limit=3,
            traffic_limit_strategy=TrafficLimitStrategy.NO_RESET,
            tag="STD",
            internal_squads=squads,
            external_squad=None,
            expire_at=datetime.now(timezone.utc) + timedelta(days=30),
            url=panel_user["subscriptionUrl"],
            plan_snapshot=_jsonable(retort.dump(snapshot)),
        )
        session.add(subscription)
        await session.flush()
        subscriber.current_subscription_id = subscription.id
        await session.commit()

        ids = SeedIds(
            owner_tg=owner_tg,
            admin_tg=ADMIN_ID,
            subscriber_tg=SUBSCRIBER_ID,
            newbie_tg=NEWBIE_ID,
            owner_id=owner.id,
            admin_id=admin.id,
            subscriber_id=subscriber.id,
            newbie_id=newbie.id,
            plan_id=plan.id,
            trial_plan_id=trial.id,
            subscription_id=subscription.id,
            subscriber_remna_id=panel_user["id"],
        )

    # DAO read caches live in Redis; start every test from a cold cache.
    await app.flush_redis()
    return ids


def _jsonable(value: object) -> object:
    import json

    from src.infrastructure.common import json as project_json

    return json.loads(project_json.encode(value))
