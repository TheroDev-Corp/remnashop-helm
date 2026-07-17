from src.application.common import Interactor
from src.application.common.remnawave import Remnawave
from src.application.dto import SquadInfoDto, UserDto


class GetInternalSquads(Interactor[None, list[SquadInfoDto]]):
    required_permission = None

    def __init__(self, remnawave: Remnawave) -> None:
        self.remnawave = remnawave

    async def _execute(self, actor: UserDto, data: None) -> list[SquadInfoDto]:
        return await self.remnawave.get_internal_squads()


class GetExternalSquads(Interactor[None, list[SquadInfoDto]]):
    required_permission = None

    def __init__(self, remnawave: Remnawave) -> None:
        self.remnawave = remnawave

    async def _execute(self, actor: UserDto, data: None) -> list[SquadInfoDto]:
        return await self.remnawave.get_external_squads()
