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
