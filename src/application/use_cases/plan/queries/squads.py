from src.application.common import Interactor, Remnawave
from src.application.common.policy import Permission
from src.application.dto import UserDto


class CheckSquadsAvailable(Interactor[None, bool]):
    required_permission = Permission.REMNASHOP_PLAN_EDITOR

    def __init__(self, remnawave: Remnawave) -> None:
        self.remnawave = remnawave

    async def _execute(self, actor: UserDto, data: None) -> bool:
        return await self.remnawave.get_squads_available()
