from typing import Optional

from src.application.common import Interactor
from src.application.common.dao import PromocodeDao
from src.application.common.policy import Permission
from src.application.dto import (
    PromocodeDetailStatisticsDto,
    PromocodeStatisticsDto,
    UserDto,
)


class GetPromocodeStatistics(Interactor[None, PromocodeStatisticsDto]):
    required_permission = Permission.VIEW_STATISTICS

    def __init__(self, promocode_dao: PromocodeDao) -> None:
        self.promocode_dao = promocode_dao

    async def _execute(self, actor: UserDto, data: None) -> PromocodeStatisticsDto:
        return await self.promocode_dao.get_statistics()


class GetPromocodeDetailStatistics(Interactor[int, Optional[PromocodeDetailStatisticsDto]]):
    required_permission = Permission.VIEW_STATISTICS

    def __init__(self, promocode_dao: PromocodeDao) -> None:
        self.promocode_dao = promocode_dao

    async def _execute(
        self, actor: UserDto, promocode_id: int
    ) -> Optional[PromocodeDetailStatisticsDto]:
        return await self.promocode_dao.get_detail_statistics(promocode_id)
