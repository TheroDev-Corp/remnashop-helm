from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from src.application.dto import UserDto as AppUserDto
from src.application.services.remnawave import RemnaUserEvent, RemnaWebhookService
from src.core.enums import Role, SubscriptionStatus, UserNotificationType
from src.infrastructure.services.remnawave import RemnawaveImpl


@pytest.fixture
def mock_uow():
    uow = MagicMock()
    uow.commit = AsyncMock()
    return uow


@pytest.fixture
def mock_user_dao():
    dao = MagicMock()
    dao.get_by_remna_id = AsyncMock(return_value=None)
    dao.get_by_remna_uuid = AsyncMock()
    dao.get_by_telegram_id = AsyncMock(return_value=None)
    dao.update = AsyncMock()
    dao.clear_current_subscription = AsyncMock()
    return dao


@pytest.fixture
def mock_sub_dao():
    dao = MagicMock()
    dao.get_by_user_id = AsyncMock()
    dao.get_by_remna_id = AsyncMock()
    dao.get_current = AsyncMock(return_value=None)
    dao.update = AsyncMock()
    dao.update_status = AsyncMock()
    return dao


@pytest.fixture
def mock_publisher():
    publisher = MagicMock()
    publisher.publish = AsyncMock()
    return publisher


@pytest.fixture
def mock_redis():
    redis = MagicMock()
    redis.client = MagicMock()
    redis.client.set = AsyncMock(return_value=True)
    return redis


@pytest.fixture
def mock_remnawave():
    remnawave = MagicMock()
    remnawave.is_owned_by = RemnawaveImpl.is_owned_by
    remnawave.get_user_by_id = AsyncMock(return_value=None)
    return remnawave


@pytest.fixture
def webhook_service(
    mock_uow, mock_user_dao, mock_sub_dao, mock_publisher, mock_redis, mock_remnawave
):
    config = MagicMock()
    bot_service = MagicMock()
    sync_user = MagicMock()
    return RemnaWebhookService(
        config=config,
        uow=mock_uow,
        user_dao=mock_user_dao,
        subscription_dao=mock_sub_dao,
        event_bus=mock_publisher,
        redis=mock_redis,
        bot_service=bot_service,
        remnawave=mock_remnawave,
        sync_user=sync_user,
    )


def _remna_user(id: int, telegram_id, username: str = "someone") -> MagicMock:
    remna_user = MagicMock()
    remna_user.id = id
    remna_user.uuid = UUID("11111111-1111-1111-1111-111111111111")
    remna_user.telegram_id = telegram_id
    remna_user.username = username
    return remna_user


def _bot_user(id: int, telegram_id) -> AppUserDto:
    return AppUserDto(
        id=id,
        telegram_id=telegram_id,
        username=f"user{id}",
        name=f"User {id}",
        email=None,
        referral_code=f"REF{id}",
        role=Role.USER,
    )


@pytest.mark.asyncio
async def test_get_user_by_remna_user_by_telegram_id(webhook_service, mock_user_dao):
    expected_user = _bot_user(1, 999999)
    mock_user_dao.get_by_telegram_id.return_value = expected_user

    found = await webhook_service._get_user_by_remna_user(_remna_user(12345, 999999))
    assert found == expected_user
    mock_user_dao.get_by_telegram_id.assert_awaited_once_with(999999)


@pytest.mark.asyncio
async def test_get_user_by_remna_user_fallback_remna_id_requires_ownership(
    webhook_service, mock_user_dao
):
    expected_user = _bot_user(2, 888888)
    mock_user_dao.get_by_remna_id.return_value = expected_user

    # Panel user without telegramId is accepted only under the username generated for this user.
    remna_user = _remna_user(12345, None, username=expected_user.remna_name)
    found = await webhook_service._get_user_by_remna_user(remna_user)

    assert found == expected_user
    mock_user_dao.get_by_remna_id.assert_awaited_once_with(12345)


@pytest.mark.asyncio
async def test_fallback_remna_id_refuses_foreign_panel_user(
    webhook_service, mock_user_dao, mock_sub_dao
):
    mock_user_dao.get_by_remna_id.return_value = _bot_user(2, 888888)

    found = await webhook_service._get_user_by_remna_user(_remna_user(149, None, "stranger"))

    assert found is None
    mock_sub_dao.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_remna_id_accepts_telegram_less_binding(
    webhook_service, mock_user_dao, mock_sub_dao
):
    # Imported panel user keeps its own username; the bot user bound to it has no telegram_id.
    expected_user = _bot_user(7, None)
    mock_user_dao.get_by_remna_id.return_value = expected_user

    found = await webhook_service._get_user_by_remna_user(_remna_user(149, None, "imported"))

    assert found == expected_user
    mock_sub_dao.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_remna_id_telegram_less_user_rejects_telegram_bound_panel_user(
    webhook_service, mock_user_dao
):
    mock_user_dao.get_by_remna_id.return_value = _bot_user(7, None)

    # telegramId in the payload routes by telegram only; no bot user has it -> no match.
    found = await webhook_service._get_user_by_remna_user(_remna_user(149, 111, "imported"))

    assert found is None
    mock_user_dao.get_by_remna_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_payload_never_falls_back_to_remna_id(
    webhook_service, mock_user_dao, mock_sub_dao
):
    mock_user_dao.get_by_telegram_id.return_value = None
    mock_user_dao.get_by_remna_id.return_value = _bot_user(2, 222)

    found = await webhook_service._get_user_by_remna_user(_remna_user(149, 111))

    assert found is None
    mock_user_dao.get_by_remna_id.assert_not_awaited()
    mock_sub_dao.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_heal_rebinds_when_stored_id_is_foreign(
    webhook_service, mock_user_dao, mock_sub_dao, mock_remnawave, sample_subscription_dto
):
    user = _bot_user(1, 999999)
    mock_user_dao.get_by_telegram_id.return_value = user
    sample_subscription_dto.user_remna_id = 149
    sample_subscription_dto._changed_data.clear()
    mock_sub_dao.get_current.return_value = sample_subscription_dto
    mock_remnawave.get_user_by_id.return_value = _remna_user(149, 111)

    await webhook_service._get_user_by_remna_user(_remna_user(500, 999999))

    mock_sub_dao.update.assert_awaited_once()
    assert mock_sub_dao.update.await_args.args[0].user_remna_id == 500


@pytest.mark.asyncio
async def test_auto_heal_keeps_valid_stored_binding(
    webhook_service, mock_user_dao, mock_sub_dao, mock_remnawave, sample_subscription_dto
):
    user = _bot_user(1, 999999)
    mock_user_dao.get_by_telegram_id.return_value = user
    sample_subscription_dto.user_remna_id = 149
    mock_sub_dao.get_current.return_value = sample_subscription_dto
    # Stored panel user still belongs to this user (duplicate panel accounts).
    mock_remnawave.get_user_by_id.return_value = _remna_user(149, 999999)

    await webhook_service._get_user_by_remna_user(_remna_user(500, 999999))

    mock_sub_dao.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_webhook_touches_only_owner_current_subscription(
    webhook_service, mock_user_dao, mock_sub_dao, mock_uow, sample_subscription_dto
):
    user = _bot_user(1, 999999)
    mock_user_dao.get_by_telegram_id.return_value = user
    sample_subscription_dto.user_remna_id = 149
    mock_sub_dao.get_current.return_value = sample_subscription_dto

    await webhook_service.handle_user_event(RemnaUserEvent.DELETED, _remna_user(149, 999999))

    mock_sub_dao.get_by_remna_id.assert_not_awaited()
    mock_sub_dao.update_status.assert_awaited_once_with(
        sample_subscription_dto.id, SubscriptionStatus.DELETED
    )
    mock_user_dao.clear_current_subscription.assert_awaited_once_with(user.id)
    mock_uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_webhook_skips_when_current_subscription_bound_elsewhere(
    webhook_service, mock_user_dao, mock_sub_dao, sample_subscription_dto
):
    mock_user_dao.get_by_telegram_id.return_value = _bot_user(1, 999999)
    sample_subscription_dto.user_remna_id = 200
    mock_sub_dao.get_current.return_value = sample_subscription_dto

    await webhook_service.handle_user_event(RemnaUserEvent.DELETED, _remna_user(149, 999999))

    mock_sub_dao.get_by_remna_id.assert_not_awaited()
    mock_sub_dao.update_status.assert_not_awaited()
    mock_sub_dao.update.assert_not_awaited()
    mock_user_dao.clear_current_subscription.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_webhook_for_foreign_panel_user_does_nothing(
    webhook_service, mock_user_dao, mock_sub_dao
):
    # Panel user 149 has no telegramId; the bot user bound to 149 in DB does not own it.
    mock_user_dao.get_by_remna_id.return_value = _bot_user(2, 222)

    await webhook_service.handle_user_event(RemnaUserEvent.DELETED, _remna_user(149, None))

    mock_sub_dao.get_current.assert_not_awaited()
    mock_sub_dao.update_status.assert_not_awaited()
    mock_user_dao.clear_current_subscription.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event", "expected_type"),
    [
        (RemnaUserEvent.EXPIRES_IN_72_HOURS, UserNotificationType.EXPIRES_IN_3_DAYS),
        (RemnaUserEvent.EXPIRES_IN_48_HOURS, UserNotificationType.EXPIRES_IN_2_DAYS),
        (RemnaUserEvent.EXPIRES_IN_24_HOURS, UserNotificationType.EXPIRES_IN_1_DAY),
    ],
)
async def test_expiring_event_uses_per_day_notification_type(
    webhook_service, mock_publisher, event, expected_type
):
    subscription = MagicMock()
    subscription.expire_at = None
    subscription.is_trial = False

    await webhook_service._process_expiring(
        _bot_user(1, 999999), subscription, event, _remna_user(12345, 999999)
    )

    published = mock_publisher.publish.await_args.args[0]
    assert published.notification_type == expected_type
