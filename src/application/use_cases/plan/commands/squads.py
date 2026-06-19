from dataclasses import dataclass

from src.application.common import Interactor, Remnawave
from src.application.common.policy import Permission
from src.application.dto import PlanDto, UserDto


@dataclass(frozen=True)
class SanitizePlanSquadsDto:
    plan: PlanDto


class SanitizePlanSquads(Interactor[SanitizePlanSquadsDto, PlanDto]):
    required_permission = Permission.REMNASHOP_PLAN_EDITOR

    def __init__(self, remnawave: Remnawave) -> None:
        self.remnawave = remnawave

    async def _execute(self, actor: UserDto, data: SanitizePlanSquadsDto) -> PlanDto:
        plan = data.plan

        if plan.internal_squads:
            valid_internal = {squad.uuid for squad in await self.remnawave.get_internal_squads()}
            plan.internal_squads = [u for u in plan.internal_squads if u in valid_internal]

        if plan.external_squad is not None:
            valid_external = {squad.uuid for squad in await self.remnawave.get_external_squads()}
            if plan.external_squad not in valid_external:
                plan.external_squad = None

        return plan
