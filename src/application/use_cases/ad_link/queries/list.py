from src.application.common import Interactor
from src.application.common.dao import AdLinkDao
from src.application.common.policy import Permission
from src.application.dto import AdLinkDto, UserDto


class GetAdLinks(Interactor[None, list[AdLinkDto]]):
    required_permission = Permission.VIEW_ADVERTISING

    def __init__(self, ad_link_dao: AdLinkDao) -> None:
        self.ad_link_dao = ad_link_dao

    async def _execute(self, actor: UserDto, data: None) -> list[AdLinkDto]:
        return await self.ad_link_dao.get_all()
