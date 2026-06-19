import json

from adaptix import Retort
from loguru import logger

from src.application.common import Interactor
from src.application.common.dao import PlanDao
from src.application.common.policy import Permission
from src.application.dto import PlanDto, UserDto


class ParsePlansImport(Interactor[str, list[PlanDto]]):
    required_permission = Permission.REMNASHOP_PLAN_EDITOR

    def __init__(self, retort: Retort) -> None:
        self.retort = retort

    async def _execute(self, actor: UserDto, raw_plans: str) -> list[PlanDto]:
        logger.debug(f"{actor.log} Parsing plans import file")

        json_plans = json.loads(raw_plans)
        if isinstance(json_plans, dict):
            raw_data = [json_plans]
        elif isinstance(json_plans, list):
            raw_data = json_plans
        else:
            raise ValueError("Import file must contain a plan object or a list of plans")

        plans = [self.retort.load(item, PlanDto) for item in raw_data]

        if not plans:
            logger.warning(f"{actor.log} Import aborted: file contains no plans")
            raise ValueError("Import file is empty")

        for plan in plans:
            plan.id = 0
            plan.created_at = None
            plan.updated_at = None

            for duration in plan.durations:
                duration.id = 0

                for price in duration.prices:
                    price.id = 0

        logger.info(f"{actor.log} Successfully parsed '{len(plans)}' plans from import")
        return plans


class ExportPlans(Interactor[list[int], str]):
    required_permission = Permission.REMNASHOP_PLAN_EDITOR

    def __init__(self, plan_dao: PlanDao, retort: Retort) -> None:
        self.plan_dao = plan_dao
        self.retort = retort

    async def _execute(self, actor: UserDto, plan_ids: list[int]) -> str:
        logger.debug(f"{actor.log} Exporting '{len(plan_ids)}' plans to JSON")

        exported_data = []
        for plan_id in plan_ids:
            plan = await self.plan_dao.get_by_id(plan_id)

            if plan:
                plan.id = 0
                plan.created_at = None
                plan.updated_at = None

                for duration in plan.durations:
                    duration.id = 0

                    for price in duration.prices:
                        price.id = 0

                exported_data.append(self.retort.dump(plan))

        if not exported_data:
            logger.warning(f"{actor.log} No plans found for export with IDs '{plan_ids}'")
            raise ValueError("No plans available for export")

        return json.dumps(exported_data, indent=4, ensure_ascii=False)
