from src.application.common import Interactor
from src.application.common.dao import AdLinkDao
from src.application.common.policy import Permission
from src.application.dto import AdLinkStatsDto, UserDto


class GetAdLinkStats(Interactor[int, AdLinkStatsDto]):
    required_permission = Permission.VIEW_ADVERTISING

    def __init__(self, ad_link_dao: AdLinkDao) -> None:
        self.ad_link_dao = ad_link_dao

    async def _execute(self, actor: UserDto, link_id: int) -> AdLinkStatsDto:
        return await self.ad_link_dao.get_stats(link_id)
