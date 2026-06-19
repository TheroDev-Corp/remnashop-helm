from dataclasses import dataclass

from loguru import logger

from src.application.common import Interactor
from src.application.common.dao import PlanDao
from src.application.common.policy import Permission
from src.application.common.uow import UnitOfWork
from src.application.dto import PlanDto, UserDto


class MovePlanUp(Interactor[int, None]):
    required_permission = Permission.REMNASHOP_PLAN_EDITOR

    def __init__(self, uow: UnitOfWork, plan_dao: PlanDao) -> None:
        self.uow = uow
        self.plan_dao = plan_dao

    async def _execute(self, actor: UserDto, plan_id: int) -> None:
        async with self.uow:
            plans = await self.plan_dao.get_all()
            plans.sort(key=lambda p: p.order_index)

            index = next((i for i, p in enumerate(plans) if p.id == plan_id), None)

            if index is None:
                logger.warning(f"Plan with ID '{plan_id}' not found for move operation")
                return

            if index == 0:
                plan = plans.pop(0)
                plans.append(plan)
                logger.debug(f"Plan '{plan_id}' moved from top to bottom")
            else:
                plans[index - 1], plans[index] = plans[index], plans[index - 1]
                logger.debug(f"Plan '{plan_id}' moved up one position")

            for i, p in enumerate(plans, start=1):
                if p.order_index != i:
                    await self.plan_dao.set_order_index(p.id, i)

            await self.uow.commit()

        logger.info(f"{actor.log} Moved plan '{plan_id}' up successfully")


@dataclass(frozen=True)
class MoveDurationUpDto:
    plan: PlanDto
    days: int


class MoveDurationUp(Interactor[MoveDurationUpDto, PlanDto]):
    required_permission = Permission.REMNASHOP_PLAN_EDITOR

    async def _execute(self, actor: UserDto, data: MoveDurationUpDto) -> PlanDto:
        plan = data.plan
        days = data.days

        durations = sorted(plan.durations, key=lambda d: d.order_index)

        index = next((i for i, d in enumerate(durations) if d.days == days), None)

        if index is None:
            logger.warning(f"Duration with '{days}' days not found in plan '{plan.id}'")
            return plan

        if index == 0:
            moved_duration = durations.pop(0)
            durations.append(moved_duration)
            logger.debug(f"Duration '{days}' moved from top to bottom in plan '{plan.id}'")
        else:
            durations[index - 1], durations[index] = durations[index], durations[index - 1]
            logger.debug(f"Duration '{days}' moved up in plan '{plan.id}'")

        for i, d in enumerate(durations, start=1):
            d.order_index = i

        plan.durations = durations
        logger.info(f"{actor.log} Reordered duration '{days}' days up in plan '{plan.id}' object")
        return plan


class DeletePlan(Interactor[int, None]):
    required_permission = Permission.REMNASHOP_PLAN_EDITOR

    def __init__(self, uow: UnitOfWork, plan_dao: PlanDao) -> None:
        self.uow = uow
        self.plan_dao = plan_dao

    async def _execute(self, actor: UserDto, plan_id: int) -> None:
        async with self.uow:
            await self.plan_dao.delete(plan_id)

            plans = await self.plan_dao.get_all()
            plans.sort(key=lambda p: p.order_index)

            for i, p in enumerate(plans, start=1):
                if p.order_index != i:
                    await self.plan_dao.set_order_index(p.id, i)

            await self.uow.commit()

        logger.info(f"{actor.log} Deleted plan ID '{plan_id}' and reordered remaining")
