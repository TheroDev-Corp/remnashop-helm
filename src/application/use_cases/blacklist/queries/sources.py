from src.application.common import Interactor
from src.application.common.dao import SettingsDao
from src.application.common.policy import Permission
from src.application.dto import BlacklistSourceDto, UserDto


class GetBlacklistSources(Interactor[None, list[BlacklistSourceDto]]):
    required_permission = Permission.BLACKLIST

    def __init__(self, settings_dao: SettingsDao) -> None:
        self.settings_dao = settings_dao

    async def _execute(self, actor: UserDto, data: None) -> list[BlacklistSourceDto]:
        settings = await self.settings_dao.get()
        return settings.blacklist.sources
