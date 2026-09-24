import re
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from src.application.common import Interactor
from src.application.common.dao import SettingsDao
from src.application.common.policy import Permission
from src.application.common.uow import UnitOfWork
from src.application.dto import SettingsDto, UserDto
from src.core.types import NotificationType
from src.core.utils.converters import normalize_channel_id


class ToggleNotification(Interactor[NotificationType, Optional[SettingsDto]]):
    required_permission = Permission.SETTINGS_NOTIFICATIONS

    def __init__(self, uow: UnitOfWork, settings_dao: SettingsDao) -> None:
        self.uow = uow
        self.settings_dao = settings_dao

    async def _execute(
        self,
        actor: UserDto,
        notification_type: NotificationType,
    ) -> Optional[SettingsDto]:
        async with self.uow:
            settings = await self.settings_dao.get()
            settings.notifications.toggle(notification_type)
            updated = await self.settings_dao.update(settings)

            await self.uow.commit()

        logger.info(f"{actor.log} Toggled notification '{notification_type}'")
        return updated


@dataclass
class UpdateSystemNotificationRouteDto:
    notification_type: NotificationType
    chat_id: Optional[int]
    thread_id: Optional[int]


class UpdateSystemNotificationRoute(
    Interactor[UpdateSystemNotificationRouteDto, Optional[SettingsDto]]
):
    required_permission = Permission.SETTINGS_NOTIFICATIONS

    def __init__(self, uow: UnitOfWork, settings_dao: SettingsDao) -> None:
        self.uow = uow
        self.settings_dao = settings_dao

    async def _execute(
        self,
        actor: UserDto,
        data: UpdateSystemNotificationRouteDto,
    ) -> Optional[SettingsDto]:
        chat_id = normalize_channel_id(data.chat_id) if data.chat_id is not None else None

        async with self.uow:
            settings = await self.settings_dao.get()
            settings.notifications.set_route(data.notification_type, chat_id, data.thread_id)
            updated = await self.settings_dao.update(settings)
            await self.uow.commit()

        logger.info(
            f"{actor.log} Updated notification route for '{data.notification_type}': "
            f"chat={chat_id}, thread={data.thread_id}"
        )
        return updated


@dataclass
class UpdateDefaultNotificationRouteDto:
    chat_id: Optional[int]
    thread_id: Optional[int]


class UpdateDefaultNotificationRoute(
    Interactor[UpdateDefaultNotificationRouteDto, Optional[SettingsDto]]
):
    required_permission = Permission.SETTINGS_NOTIFICATIONS

    def __init__(self, uow: UnitOfWork, settings_dao: SettingsDao) -> None:
        self.uow = uow
        self.settings_dao = settings_dao

    async def _execute(
        self,
        actor: UserDto,
        data: UpdateDefaultNotificationRouteDto,
    ) -> Optional[SettingsDto]:
        chat_id = normalize_channel_id(data.chat_id) if data.chat_id is not None else None

        async with self.uow:
            settings = await self.settings_dao.get()
            settings.notifications.set_default_route(chat_id, data.thread_id)
            updated = await self.settings_dao.update(settings)
            await self.uow.commit()

        logger.info(
            f"{actor.log} Updated default notification route: "
            f"chat={chat_id}, thread={data.thread_id}"
        )
        return updated


class ToggleExpiryReminderFallback(Interactor[None, Optional[SettingsDto]]):
    required_permission = Permission.SETTINGS_NOTIFICATIONS

    def __init__(self, uow: UnitOfWork, settings_dao: SettingsDao) -> None:
        self.uow = uow
        self.settings_dao = settings_dao

    async def _execute(self, actor: UserDto, data: None) -> Optional[SettingsDto]:
        async with self.uow:
            settings = await self.settings_dao.get()
            reminder = settings.notifications.expiry_reminder
            reminder.fallback_enabled = not reminder.fallback_enabled
            updated = await self.settings_dao.update(settings)
            await self.uow.commit()

        logger.info(f"{actor.log} Toggled expiry reminder fallback: {reminder.fallback_enabled}")
        return updated


EXPIRY_REMINDER_DAY_MIN = 1
EXPIRY_REMINDER_DAY_MAX = 30
EXPIRY_REMINDER_DAYS_LIMIT = 5


def parse_expiry_reminder_days(text: str) -> list[int]:
    """Parse "7, 3 1" into [7, 3, 1]; ValueError when empty, too long or out of range."""
    days = sorted({int(part) for part in re.split(r"[\s,;]+", text.strip()) if part}, reverse=True)
    if not days or len(days) > EXPIRY_REMINDER_DAYS_LIMIT:
        raise ValueError
    if days[-1] < EXPIRY_REMINDER_DAY_MIN or days[0] > EXPIRY_REMINDER_DAY_MAX:
        raise ValueError
    return days


class UpdateExpiryReminderDays(Interactor[str, Optional[SettingsDto]]):
    required_permission = Permission.SETTINGS_NOTIFICATIONS

    def __init__(self, uow: UnitOfWork, settings_dao: SettingsDao) -> None:
        self.uow = uow
        self.settings_dao = settings_dao

    async def _execute(self, actor: UserDto, input_days: str) -> Optional[SettingsDto]:
        days = parse_expiry_reminder_days(input_days)

        async with self.uow:
            settings = await self.settings_dao.get()
            settings.notifications.expiry_reminder.days = days
            updated = await self.settings_dao.update(settings)
            await self.uow.commit()

        logger.info(f"{actor.log} Set expiry reminder days: {days}")
        return updated
