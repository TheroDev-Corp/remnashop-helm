from src.application.common import Cryptographer, Interactor
from src.application.common.dao import AdLinkDao
from src.application.common.policy import Permission
from src.application.dto import UserDto


class GenerateAdLinkCode(Interactor[None, str]):
    required_permission = Permission.VIEW_ADVERTISING

    def __init__(self, ad_link_dao: AdLinkDao, cryptographer: Cryptographer) -> None:
        self.ad_link_dao = ad_link_dao
        self.cryptographer = cryptographer

    async def _execute(self, actor: UserDto, data: None) -> str:
        return await self.cryptographer.generate_unique_code(self.ad_link_dao.get_by_code)
