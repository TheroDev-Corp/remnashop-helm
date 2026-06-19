from src.application.common import Interactor
from src.application.common.dao import RecentActivityDao
from src.application.dto import UserDto


class TrackUserActivity(Interactor[int, None]):
    required_permission = None

    def __init__(self, recent_activity_dao: RecentActivityDao) -> None:
        self.recent_activity_dao = recent_activity_dao

    async def _execute(self, actor: UserDto, user_id: int) -> None:
        await self.recent_activity_dao.touch(user_id)
