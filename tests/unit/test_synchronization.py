from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from remnapy.enums import TrafficLimitStrategy
from remnapy.models import UserResponseDto

from src.application.dto import SubscriptionDto, UserDto
from src.application.use_cases.remnawave.commands.synchronization import (
    SyncAllUsersFromBot,
    SyncRemnaUser,
    SyncRemnaUserDto,
)
from src.application.use_cases.subscription.commands.sync import (
    SyncSubscriptionFromRemnashop,
    SyncSubscriptionFromRemnawave,
)
from src.core.enums import Role, SubscriptionStatus
from src.core.exceptions import RemnaUserBindingError
from src.infrastructure.services.remnawave import RemnawaveImpl

NOW = datetime.now(timezone.utc)
SYSTEM = UserDto(id=0, name="System", role=Role.SYSTEM, created_at=NOW, updated_at=NOW)


@pytest.fixture
def mock_uow():
    uow = MagicMock()
    uow.commit = AsyncMock()
    uow.persist_with_unique_code = AsyncMock()
    return uow


@pytest.fixture
def mock_user_dao():
    dao = MagicMock()
    dao.get_by_id = AsyncMock()
    dao.get_by_remna_id = AsyncMock(return_value=None)
    dao.get_by_remna_uuid = AsyncMock()
    dao.get_by_telegram_id = AsyncMock(return_value=None)
    dao.update = AsyncMock()
    dao.create = AsyncMock()
    dao.clear_current_subscription = AsyncMock()
    dao.get_all = AsyncMock(return_value=[])
    return dao


@pytest.fixture
def mock_sub_dao():
    dao = MagicMock()
    dao.get_by_user_id = AsyncMock()
    dao.get_current = AsyncMock(return_value=None)
    dao.update = AsyncMock()
    dao.update_status = AsyncMock()
    dao.create = AsyncMock()
    dao.ensure_remna_id_available = AsyncMock()
    return dao


@pytest.fixture
def mock_remnawave():
    remna = MagicMock()
    remna.apply_sync = MagicMock()
    remna.is_owned_by = RemnawaveImpl.is_owned_by
    remna.resolve_user = AsyncMock(return_value=None)
    remna.get_user_by_id = AsyncMock(return_value=None)
    remna.get_users_by_telegram_id = AsyncMock(return_value=[])
    remna.update_user = AsyncMock()
    remna.create_user = AsyncMock()
    remna.delete_user = AsyncMock()
    return remna


def _sync_use_case(uow, user_dao, sub_dao, remnawave) -> SyncRemnaUser:
    return SyncRemnaUser(
        uow=uow,
        user_dao=user_dao,
        subscription_dao=sub_dao,
        config=MagicMock(),
        remnawave=remnawave,
        cryptographer=MagicMock(),
    )


def _remna_user(id: int = 500, telegram_id=999999, username: str = "sync_test") -> MagicMock:
    remna_user = MagicMock(spec=UserResponseDto)
    remna_user.id = id
    remna_user.uuid = UUID("11111111-1111-1111-1111-111111111111")
    remna_user.telegram_id = telegram_id
    remna_user.username = username
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
    return remna_user


def _bot_user(id: int = 1, telegram_id=999999) -> UserDto:
    return UserDto(
        id=id,
        telegram_id=telegram_id,
        username="sync_test",
        name="Sync Test",
        email=None,
        referral_code="REF",
        role=Role.USER,
        created_at=NOW,
        updated_at=NOW,
    )


def _subscription(user_remna_id: int = 500, user_id: int = 1) -> SubscriptionDto:
    return SubscriptionDto(
        id=1,
        user_id=user_id,
        user_remna_id=user_remna_id,
        status=SubscriptionStatus.ACTIVE,
        expire_at=datetime(2026, 12, 31, 0, 0, 0, tzinfo=timezone.utc),
        traffic_limit=1.0,
        device_limit=2,
        traffic_limit_strategy=TrafficLimitStrategy.NO_RESET,
        tag="TAG",
        internal_squads=[],
        external_squad=None,
        url="https://sub.example.com/sync",
        plan_snapshot=MagicMock(),
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_sync_remna_user_found_by_id(mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave):
    use_case = _sync_use_case(mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave)
    local_sub = _subscription()

    mock_user_dao.get_by_telegram_id.return_value = _bot_user()
    mock_sub_dao.get_current.return_value = local_sub
    mock_remnawave.apply_sync.return_value = local_sub

    await use_case(SYSTEM, SyncRemnaUserDto(remna_user=_remna_user(), creating=False))

    mock_user_dao.get_by_telegram_id.assert_awaited_once_with(999999)
    mock_sub_dao.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_remna_user_never_binds_other_telegram_user_via_remna_id(
    mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave
):
    use_case = _sync_use_case(mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave)
    mock_user_dao.get_by_telegram_id.return_value = None
    mock_user_dao.get_by_remna_id.return_value = _bot_user(id=2, telegram_id=222)
    mock_sub_dao.get_current.return_value = _subscription(user_remna_id=149, user_id=2)

    result = await use_case(
        SYSTEM, SyncRemnaUserDto(remna_user=_remna_user(149, 111), creating=False)
    )

    assert result is False
    mock_user_dao.get_by_remna_id.assert_not_awaited()
    mock_sub_dao.get_current.assert_not_awaited()
    mock_sub_dao.update.assert_not_awaited()
    mock_sub_dao.create.assert_not_awaited()
    mock_remnawave.apply_sync.assert_not_called()


@pytest.mark.asyncio
async def test_sync_remna_user_without_telegram_refuses_foreign_db_binding(
    mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave
):
    use_case = _sync_use_case(mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave)
    mock_user_dao.get_by_remna_id.return_value = _bot_user(id=2, telegram_id=222)

    result = await use_case(
        SYSTEM, SyncRemnaUserDto(remna_user=_remna_user(149, None, "stranger"), creating=True)
    )

    assert result is False
    mock_uow.persist_with_unique_code.assert_not_awaited()
    mock_sub_dao.update.assert_not_awaited()
    mock_sub_dao.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_remna_user_keeps_valid_binding_for_duplicate_panel_user(
    mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave
):
    use_case = _sync_use_case(mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave)
    mock_user_dao.get_by_telegram_id.return_value = _bot_user()
    mock_sub_dao.get_current.return_value = _subscription(user_remna_id=500)
    mock_remnawave.get_user_by_id.return_value = _remna_user(500, 999999)

    result = await use_case(SYSTEM, SyncRemnaUserDto(_remna_user(777, 999999), creating=False))

    assert result is False
    mock_remnawave.apply_sync.assert_not_called()
    mock_sub_dao.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_import_creates_telegram_less_user(
    mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave
):
    use_case = _sync_use_case(mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave)
    created = _bot_user(id=7, telegram_id=None)
    mock_uow.persist_with_unique_code.return_value = created

    await use_case(SYSTEM, SyncRemnaUserDto(_remna_user(149, None, "imported"), creating=True))

    mock_user_dao.get_by_remna_id.assert_awaited_once_with(149)
    mock_uow.persist_with_unique_code.assert_awaited_once()
    mock_sub_dao.create.assert_awaited_once()
    new_sub, user_id = mock_sub_dao.create.await_args.args
    assert (new_sub.user_remna_id, user_id) == (149, created.id)
    mock_uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_recognises_existing_telegram_less_binding(
    mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave
):
    use_case = _sync_use_case(mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave)
    local_sub = _subscription(user_remna_id=149, user_id=7)
    mock_user_dao.get_by_remna_id.return_value = _bot_user(id=7, telegram_id=None)
    mock_sub_dao.get_current.return_value = local_sub
    mock_remnawave.apply_sync.return_value = local_sub

    await use_case(SYSTEM, SyncRemnaUserDto(_remna_user(149, None, "imported"), creating=False))

    mock_remnawave.apply_sync.assert_called_once()
    mock_sub_dao.update.assert_awaited_once_with(local_sub)
    mock_uow.persist_with_unique_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_from_remnashop_checks_binding_before_panel_update(
    mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave
):
    use_case = SyncSubscriptionFromRemnashop(
        uow=mock_uow,
        user_dao=mock_user_dao,
        subscription_dao=mock_sub_dao,
        remnawave=mock_remnawave,
    )
    mock_user_dao.get_by_id.return_value = _bot_user()
    mock_sub_dao.get_current.return_value = _subscription(user_remna_id=149)
    mock_remnawave.resolve_user.return_value = _remna_user(500, 999999)
    mock_sub_dao.ensure_remna_id_available.side_effect = RemnaUserBindingError("taken")

    with pytest.raises(RemnaUserBindingError):
        await use_case(SYSTEM, 1)

    mock_remnawave.update_user.assert_not_awaited()
    mock_remnawave.create_user.assert_not_awaited()
    mock_uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_from_remnawave_does_not_delete_on_panel_error(
    mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave
):
    use_case = SyncSubscriptionFromRemnawave(
        uow=mock_uow,
        user_dao=mock_user_dao,
        subscription_dao=mock_sub_dao,
        remnawave=mock_remnawave,
        sync_remna_user=MagicMock(),
    )
    mock_user_dao.get_by_id.return_value = _bot_user()
    mock_sub_dao.get_current.return_value = _subscription()
    mock_remnawave.resolve_user.side_effect = RuntimeError("panel returned 502")

    with pytest.raises(RuntimeError):
        await use_case(SYSTEM, 1)

    mock_sub_dao.update_status.assert_not_awaited()
    mock_user_dao.clear_current_subscription.assert_not_awaited()
    mock_uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_from_remnawave_deletes_only_when_resolve_finds_nothing(
    mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave
):
    use_case = SyncSubscriptionFromRemnawave(
        uow=mock_uow,
        user_dao=mock_user_dao,
        subscription_dao=mock_sub_dao,
        remnawave=mock_remnawave,
        sync_remna_user=MagicMock(),
    )
    user = _bot_user()
    mock_user_dao.get_by_id.return_value = user
    mock_sub_dao.get_current.return_value = _subscription(user_remna_id=149)
    mock_remnawave.resolve_user.return_value = None

    await use_case(SYSTEM, 1)

    mock_remnawave.resolve_user.assert_awaited_once_with(user, 149)
    mock_remnawave.get_users_by_telegram_id.assert_not_awaited()
    mock_sub_dao.update_status.assert_awaited_once_with(1, SubscriptionStatus.DELETED)
    mock_user_dao.clear_current_subscription.assert_awaited_once_with(user.id)


@pytest.mark.asyncio
async def test_sync_from_remnashop_persists_re_resolved_id(
    mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave
):
    use_case = SyncSubscriptionFromRemnashop(
        uow=mock_uow,
        user_dao=mock_user_dao,
        subscription_dao=mock_sub_dao,
        remnawave=mock_remnawave,
    )
    user = _bot_user()
    subscription = _subscription(user_remna_id=149)
    mock_user_dao.get_by_id.return_value = user
    mock_sub_dao.get_current.return_value = subscription
    mock_remnawave.resolve_user.return_value = _remna_user(500, 999999)
    mock_remnawave.update_user.return_value = _remna_user(500, 999999)

    await use_case(SYSTEM, 1)

    mock_remnawave.update_user.assert_awaited_once_with(
        user=user, id=500, subscription=subscription
    )
    mock_sub_dao.update.assert_awaited_once()
    assert mock_sub_dao.update.await_args.args[0].user_remna_id == 500


@pytest.mark.asyncio
async def test_sync_all_from_bot_binds_created_user_directly(
    mock_uow, mock_user_dao, mock_sub_dao, mock_remnawave
):
    use_case = SyncAllUsersFromBot(
        uow=mock_uow,
        user_dao=mock_user_dao,
        subscription_dao=mock_sub_dao,
        remnawave=mock_remnawave,
    )
    user = _bot_user(telegram_id=None)
    mock_user_dao.get_all.return_value = [user]
    mock_sub_dao.get_current.return_value = _subscription(user_remna_id=149)
    mock_remnawave.resolve_user.return_value = None
    created = _remna_user(900, None, user.remna_name)
    mock_remnawave.create_user.return_value = created

    result = await use_case(SYSTEM)

    assert result["recreated"] == 1
    assert result["errors"] == 0
    mock_sub_dao.update.assert_awaited_once()
    assert mock_sub_dao.update.await_args.args[0].user_remna_id == 900
    mock_user_dao.get_by_remna_id.assert_not_awaited()
