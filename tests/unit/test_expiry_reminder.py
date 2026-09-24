from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.dto import SettingsDto
from src.application.services import ExpiryReminderService
from src.application.use_cases.settings.commands.notifications import parse_expiry_reminder_days
from src.core.enums import UserNotificationType
from src.core.utils.time import datetime_now


class FakeRedis:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def set(self, key: str, value: object, nx: bool = False, ex: int = 0) -> bool:
        if nx and key in self.keys:
            return False
        self.keys.add(key)
        return True


def _service(settings: SettingsDto, subscriptions=(), users=()) -> ExpiryReminderService:
    settings_dao = MagicMock(get=AsyncMock(return_value=settings))
    subscription_dao = MagicMock(get_expiring_current=AsyncMock(return_value=list(subscriptions)))
    user_dao = MagicMock(get_by_ids=AsyncMock(return_value=list(users)))
    return ExpiryReminderService(
        settings_dao=settings_dao,
        subscription_dao=subscription_dao,
        user_dao=user_dao,
        event_bus=MagicMock(publish=AsyncMock()),
        redis=FakeRedis(),
    )


def _published(service: ExpiryReminderService) -> list:
    return [call.args[0] for call in service.event_bus.publish.await_args_list]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("day", "expected_type"),
    [
        (7, UserNotificationType.EXPIRES_IN_3_DAYS),
        (3, UserNotificationType.EXPIRES_IN_3_DAYS),
        (2, UserNotificationType.EXPIRES_IN_2_DAYS),
        (1, UserNotificationType.EXPIRES_IN_1_DAY),
    ],
)
async def test_remind_maps_day_to_its_own_toggle(
    sample_user_dto, sample_subscription_dto, day, expected_type
):
    settings = SettingsDto()
    settings.notifications.expiry_reminder.days = [7, 3, 2, 1]
    service = _service(settings)

    assert await service.remind(sample_user_dto, sample_subscription_dto, day)
    [event] = _published(service)
    assert event.notification_type == expected_type
    assert event.day == day


@pytest.mark.asyncio
async def test_remind_skips_days_not_configured(sample_user_dto, sample_subscription_dto):
    settings = SettingsDto()
    settings.notifications.expiry_reminder.days = [7, 1]
    service = _service(settings)

    assert not await service.remind(sample_user_dto, sample_subscription_dto, 3)
    assert _published(service) == []


@pytest.mark.asyncio
async def test_webhook_and_fallback_send_each_reminder_once(
    sample_user_dto, sample_subscription_dto
):
    subscription = replace(
        sample_subscription_dto, expire_at=datetime_now() + timedelta(days=2, hours=20)
    )
    service = _service(SettingsDto(), [subscription], [sample_user_dto])

    assert await service.remind(sample_user_dto, subscription, 3)  # webhook first
    assert await service.check_expiring() == 0  # fallback sees it was sent
    assert len(_published(service)) == 1

    # Renewal moves expire_at: the reminder is armed again for the new period.
    renewed = replace(subscription, expire_at=subscription.expire_at + timedelta(days=30))
    assert await service.remind(sample_user_dto, renewed, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remaining", "expected_day"),
    [
        (timedelta(days=6, hours=23), 7),
        (timedelta(days=2, hours=23), 3),
        (timedelta(hours=5), 1),  # after downtime: only the nearest reminder, not 7+3+1
    ],
)
async def test_fallback_sends_nearest_crossed_day(
    sample_user_dto, sample_subscription_dto, remaining, expected_day
):
    settings = SettingsDto()
    settings.notifications.expiry_reminder.days = [7, 3, 1]
    subscription = replace(sample_subscription_dto, expire_at=datetime_now() + remaining)
    service = _service(settings, [subscription], [sample_user_dto])

    assert await service.check_expiring() == 1
    [event] = _published(service)
    assert event.day == expected_day


@pytest.mark.asyncio
async def test_fallback_disabled_does_nothing(sample_user_dto, sample_subscription_dto):
    settings = SettingsDto()
    settings.notifications.expiry_reminder.fallback_enabled = False
    service = _service(settings, [sample_subscription_dto], [sample_user_dto])

    assert await service.check_expiring() == 0
    service.subscription_dao.get_expiring_current.assert_not_awaited()


def test_parse_expiry_reminder_days():
    assert parse_expiry_reminder_days("1, 7 3;3") == [7, 3, 1]
    for bad in ("", "0", "31", "a", "1 2 3 4 5 6"):
        with pytest.raises(ValueError):
            parse_expiry_reminder_days(bad)
