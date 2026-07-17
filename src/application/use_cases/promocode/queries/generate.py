from src.application.common import Cryptographer, Interactor
from src.application.common.dao import PromocodeDao
from src.application.common.policy import Permission
from src.application.dto import UserDto


class GeneratePromocodeCode(Interactor[None, str]):
    required_permission = Permission.MANAGE_PROMOCODE

    def __init__(self, promocode_dao: PromocodeDao, cryptographer: Cryptographer) -> None:
        self.promocode_dao = promocode_dao
        self.cryptographer = cryptographer

    async def _execute(self, actor: UserDto, data: None) -> str:
        return await self.cryptographer.generate_unique_code(self.promocode_dao.get_by_code)
