from src.application.common import Interactor
from src.application.common.dao import AdLinkDao
from src.application.dto import UserDto


class ValidateAdLinkCode(Interactor[str, bool]):
    required_permission = None

    def __init__(self, ad_link_dao: AdLinkDao) -> None:
        self.ad_link_dao = ad_link_dao

    async def _execute(self, actor: UserDto, code: str) -> bool:
        link = await self.ad_link_dao.get_by_code(code)
        return link is not None and link.is_active
