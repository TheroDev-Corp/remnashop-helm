from loguru import logger

from src.application.common import Interactor
from src.application.common.dao import SettingsDao
from src.application.common.uow import UnitOfWork
from src.application.dto import UserDto


class CreateDefaultSettings(Interactor[None, None]):
    required_permission = None

    def __init__(self, uow: UnitOfWork, settings_dao: SettingsDao) -> None:
        self.uow = uow
        self.settings_dao = settings_dao

    async def _execute(self, actor: UserDto, data: None) -> None:
        async with self.uow:
            if await self.settings_dao.exists():
                return

            await self.settings_dao.create_default()
            await self.uow.commit()

        logger.info("Created default settings")
