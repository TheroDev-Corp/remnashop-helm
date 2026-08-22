from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from src.application.dto import UserDto as AppUserDto
from src.application.services.remnawave import RemnaWebhookService
from src.core.enums import Role


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
def webhook_service(mock_uow, mock_user_dao, mock_sub_dao, mock_publisher, mock_redis):
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
        sync_user=sync_user,
    )


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_get_user_by_remna_user_by_telegram_id(webhook_service, mock_user_dao):
    mock_remna_user = MagicMock()
    mock_remna_user.id = 12345
    mock_remna_user.uuid = UUID("11111111-1111-1111-1111-111111111111")
    mock_remna_user.telegram_id = 999999

    expected_user = AppUserDto(
        id=1,
        telegram_id=999999,
        username="john",
        name="John",
        email=None,
        referral_code="REF",
        role=Role.USER,
    )
    mock_user_dao.get_by_telegram_id.return_value = expected_user

    found = await webhook_service._get_user_by_remna_user(mock_remna_user)
    assert found == expected_user
    mock_user_dao.get_by_telegram_id.assert_awaited_once_with(999999)


@pytest.mark.asyncio
async def test_get_user_by_remna_user_fallback_remna_id(webhook_service, mock_user_dao):
    mock_remna_user = MagicMock()
    mock_remna_user.id = 12345
    mock_remna_user.uuid = UUID("11111111-1111-1111-1111-111111111111")
    mock_remna_user.telegram_id = None

    expected_user = AppUserDto(
        id=2,
        telegram_id=888888,
        username="jane",
        name="Jane",
        email=None,
        referral_code="REF2",
        role=Role.USER,
    )
    mock_user_dao.get_by_remna_id.return_value = expected_user

    found = await webhook_service._get_user_by_remna_user(mock_remna_user)
    assert found == expected_user
    mock_user_dao.get_by_remna_id.assert_awaited_once_with(12345)
