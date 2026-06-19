from typing import Optional

from src.application.common import Interactor
from src.application.common.dao import RecentActivityDao, UserDao
from src.application.dto import UserDto
from src.core.constants import RECENT_ACTIVITY_MAX_COUNT


class GetRecentActivityUsers(Interactor[Optional[list[int]], list[UserDto]]):
    required_permission = None

    def __init__(self, recent_activity_dao: RecentActivityDao, user_dao: UserDao) -> None:
        self.recent_activity_dao = recent_activity_dao
        self.user_dao = user_dao

    async def _execute(self, actor: UserDto, excluded_ids: Optional[list[int]]) -> list[UserDto]:
        ids = await self.recent_activity_dao.get_recent_ids(
            RECENT_ACTIVITY_MAX_COUNT,
            excluded_ids or (),
        )
        if not ids:
            return []

        users = {user.id: user for user in await self.user_dao.get_by_ids(ids)}
        return [users[user_id] for user_id in ids if user_id in users]
