from dataclasses import dataclass
from typing import Optional

from loguru import logger

from src.application.common import Interactor
from src.application.common.dao import PromocodeDao
from src.application.common.policy import Permission
from src.application.dto import PromocodeDto, UserDto


class GetPromocode(Interactor[int, Optional[PromocodeDto]]):
    required_permission = Permission.VIEW_PROMOCODE

    def __init__(self, promocode_dao: PromocodeDao) -> None:
        self.promocode_dao = promocode_dao

    async def _execute(self, actor: UserDto, promocode_id: int) -> Optional[PromocodeDto]:
        promo = await self.promocode_dao.get_by_id(promocode_id)
        if not promo:
            logger.info(f"{actor.log} Promocode id={promocode_id} not found")
        return promo


@dataclass(frozen=True)
class GetPromocodeListDto:
    limit: int = 20
    offset: int = 0


class GetPromocodeList(Interactor[GetPromocodeListDto, list[PromocodeDto]]):
    required_permission = Permission.VIEW_PROMOCODE

    def __init__(self, promocode_dao: PromocodeDao) -> None:
        self.promocode_dao = promocode_dao

    async def _execute(self, actor: UserDto, data: GetPromocodeListDto) -> list[PromocodeDto]:
        return await self.promocode_dao.get_list(limit=data.limit, offset=data.offset)
