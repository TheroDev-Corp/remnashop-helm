import os
from datetime import datetime, timezone
from uuid import UUID

import pytest

# Ensure required environment variables for test execution
os.environ.setdefault("BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
os.environ.setdefault("BOT_SECRET_TOKEN", "secret123456789012345678901234567890")
os.environ.setdefault("BOT_OWNER_ID", "123456789")
os.environ.setdefault("BOT_SUPPORT_USERNAME", "support_test")
os.environ.setdefault("DATABASE_PASSWORD", "postgres")
os.environ.setdefault("DATABASE_USER", "postgres")
os.environ.setdefault("DATABASE_NAME", "postgres")
os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("DATABASE_PORT", "5432")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("REDIS_PASSWORD", "redis")
os.environ.setdefault("REMNAWAVE_TOKEN", "remna_token")
os.environ.setdefault("REMNAWAVE_URL", "https://remna.example.com")
os.environ.setdefault("REMNAWAVE_WEBHOOK_SECRET", "webhook_secret_key_12345")
os.environ.setdefault("APP_DOMAIN", "bot.example.com")
os.environ.setdefault("APP_CRYPT_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from remnapy.enums import TrafficLimitStrategy

from src.application.dto import PlanSnapshotDto, SubscriptionDto, UserDto
from src.core.enums import PlanType, Role, SubscriptionStatus


@pytest.fixture
def sample_user_dto() -> UserDto:
    now = datetime.now(timezone.utc)
    return UserDto(
        id=1,
        telegram_id=123456789,
        username="john_doe",
        name="John Doe",
        email="john@example.com",
        referral_code="REF123",
        role=Role.USER,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def sample_plan_dto() -> PlanSnapshotDto:
    return PlanSnapshotDto(
        id=1,
        name="Standard 30 Days",
        tag="STANDARD",
        type=PlanType.BOTH,
        duration=30,
        traffic_limit=100.0,
        device_limit=3,
        traffic_limit_strategy=TrafficLimitStrategy.NO_RESET,
        internal_squads=[UUID("11111111-1111-1111-1111-111111111111")],
        external_squad=None,
    )



@pytest.fixture
def sample_subscription_dto(sample_plan_dto) -> SubscriptionDto:
    now = datetime.now(timezone.utc)
    return SubscriptionDto(
        id=1,
        user_id=1,
        user_remna_id=12345,
        status=SubscriptionStatus.ACTIVE,
        expire_at=datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        traffic_limit=50.0,
        device_limit=2,
        traffic_limit_strategy=TrafficLimitStrategy.NO_RESET,
        tag="PREMIUM",
        internal_squads=[UUID("22222222-2222-2222-2222-222222222222")],
        external_squad=None,
        url="https://sub.example.com/token",
        plan_snapshot=sample_plan_dto,
        created_at=now,
        updated_at=now,
    )

