from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from remnapy.enums import TrafficLimitStrategy
from remnapy.models import UserResponseDto

from src.application.dto import SubscriptionDto, UserDto
from src.application.use_cases.remnawave.commands.synchronization import (
    SyncRemnaUser,
    SyncRemnaUserDto,
)
from src.core.enums import Role, SubscriptionStatus


@pytest.fixture
def mock_uow():
    uow = MagicMock()
    uow.commit = AsyncMock()
    return uow


@pytest.fixture
def mock_user_dao():
    dao = MagicMock()
    dao.get_by_remna_id = AsyncMock()
    dao.get_by_remna_uuid = AsyncMock()
    dao.get_by_telegram_id = AsyncMock()
    dao.update = AsyncMock()
    return dao


@pytest.fixture
def mock_sub_dao():
    dao = MagicMock()
    dao.get_by_user_id = AsyncMock()
    dao.get_current = AsyncMock()
    dao.update = AsyncMock()
    return dao


@pytest.fixture
def mock_remnawave():
    remna = MagicMock()
    remna.apply_sync = MagicMock()
    return remna


@pytest.mark.asyncio
async def test_sync_remna_user_found_by_id(mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave):
    config = MagicMock()
    cryptographer = MagicMock()
    use_case = SyncRemnaUser(
        uow=mock_uow,
        user_dao=mock_user_dao,
        subscription_dao=mock_sub_dao,
        config=config,
        remnawave=mock_remnawave,
        cryptographer=cryptographer,
    )

    remna_user = MagicMock(spec=UserResponseDto)
    remna_user.id = 500
    remna_user.uuid = UUID("11111111-1111-1111-1111-111111111111")
    remna_user.telegram_id = 999999
    remna_user.username = "sync_test"
    remna_user.status = "ACTIVE"
    remna_user.traffic_limit_strategy = "NO_RESET"
    remna_user.traffic_limit_bytes = 1000000
    remna_user.expire_at = datetime(2026, 12, 31, 0, 0, 0, tzinfo=timezone.utc)
    remna_user.hwid_device_limit = 2
    remna_user.tag = "TAG"
    remna_user.active_internal_squads = []
    remna_user.external_squad_uuid = None
    remna_user.subscription_url = "https://sub.example.com/sync"
    remna_user.used_traffic_bytes = 0
    remna_user.lifetime_used_traffic_bytes = 0

    now = datetime.now(timezone.utc)
    local_user = UserDto(
        id=1,
        telegram_id=999999,
        username="sync_test",
        name="Sync Test",
        email=None,
        referral_code="REF",
        role=Role.USER,
        created_at=now,
        updated_at=now,
    )
    plan_snapshot = MagicMock()
    local_sub = SubscriptionDto(
        id=1,
        user_id=1,
        user_remna_id=500,
        status=SubscriptionStatus.ACTIVE,
        expire_at=datetime(2026, 12, 31, 0, 0, 0, tzinfo=timezone.utc),
        traffic_limit=1.0,
        device_limit=2,
        traffic_limit_strategy=TrafficLimitStrategy.NO_RESET,
        tag="TAG",
        internal_squads=[],
        external_squad=None,
        url="https://sub.example.com/sync",
        plan_snapshot=plan_snapshot,
        created_at=now,
        updated_at=now,
    )

    mock_user_dao.get_by_telegram_id.return_value = local_user
    mock_sub_dao.get_by_user_id.return_value = local_sub
    mock_sub_dao.get_current = AsyncMock(return_value=local_sub)
    mock_remnawave.apply_sync.return_value = local_sub

    actor = UserDto(
        id=0,
        name="System",
        role=Role.SYSTEM,
        created_at=now,
        updated_at=now,
    )
    await use_case(actor, SyncRemnaUserDto(remna_user=remna_user, creating=False))

    mock_user_dao.get_by_telegram_id.assert_awaited_once_with(999999)
