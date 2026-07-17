from dataclasses import dataclass

from loguru import logger

from src.application.common import Interactor
from src.application.common.dao import UserDao
from src.application.common.policy import Permission
from src.application.common.uow import UnitOfWork
from src.application.dto import UserDto
from src.core.enums import Role
from src.core.exceptions import PermissionDeniedError, UserNotFoundError


@dataclass(frozen=True)
class GetAdminsResultDto:
    user_id: int
    telegram_id: int
    name: str
    role: Role
    is_deletable: bool


class GetAdmins(Interactor[None, list[GetAdminsResultDto]]):
    required_permission = Permission.MANAGE_ADMINS

    def __init__(self, user_dao: UserDao) -> None:
        self.user_dao = user_dao

    async def _execute(self, actor: UserDto, data: None) -> list[GetAdminsResultDto]:
        target_roles = [Role.OWNER] + Role.OWNER.get_subordinates()
        admins = await self.user_dao.filter_by_role(role=target_roles)

        logger.info(f"{actor.log} Retrieved admins list for management")

        result = []
        for admin in admins[::-1]:
            # Web-only admins (no telegram_id) cannot be displayed or acted upon in the bot UI
            if admin.telegram_id is None:
                continue
            result.append(
                GetAdminsResultDto(
                    user_id=admin.id,
                    telegram_id=admin.telegram_id,
                    name=admin.name,
                    role=admin.role,
                    is_deletable=self._is_deletable(actor, admin),
                )
            )
        return result

    def _is_deletable(self, actor: UserDto, target: UserDto) -> bool:
        return target.id != actor.id and target.role != Role.OWNER and actor.role > target.role


class RevokeRole(Interactor[int, None]):
    required_permission = Permission.REVOKE_ROLE

    def __init__(
        self,
        uow: UnitOfWork,
        user_dao: UserDao,
    ) -> None:
        self.uow = uow
        self.user_dao = user_dao

    async def _execute(self, actor: UserDto, user_id: int) -> None:
        async with self.uow:
            target_user = await self.user_dao.get_by_id(user_id)

            if not target_user:
                logger.warning(f"User '{user_id}' not found for role revocation")
                raise UserNotFoundError(user_id)

            if actor.id == target_user.id:
                logger.warning(f"{actor.log} tried to revoke their own role")
                raise PermissionDeniedError()

            if not actor.role > target_user.role:
                logger.warning(
                    f"User '{actor.remna_name}' ({actor.role}) tried to revoke role "
                    f"from '{target_user.remna_name}' ({target_user.role})"
                )
                raise PermissionDeniedError()

            if target_user.role == Role.OWNER:
                logger.warning(f"Attempt to revoke role from OWNER '{user_id}' blocked")
                raise PermissionDeniedError()

            target_user.role = Role.USER
            await self.user_dao.update(target_user)
            await self.uow.commit()

            logger.info(
                f"Role for user '{user_id}' revoked to '{Role.USER}' by '{actor.remna_name}'"
            )


@dataclass(frozen=True)
class SetUserRoleDto:
    user_id: int
    role: Role


class SetUserRole(Interactor[SetUserRoleDto, None]):
    required_permission = Permission.ASSIGN_ROLE

    def __init__(self, uow: UnitOfWork, user_dao: UserDao) -> None:
        self.uow = uow
        self.user_dao = user_dao

    async def _execute(self, actor: UserDto, data: SetUserRoleDto) -> None:
        # Read and mutate within one transaction (like RevokeRole) to close the
        # TOCTOU window between the role check and the update.
        async with self.uow:
            target_user = await self.user_dao.get_by_id(data.user_id)

            if not target_user:
                logger.warning(
                    f"{actor.log} Attempted to change role for non-existent user '{data.user_id}'"
                )
                raise UserNotFoundError(data.user_id)

            if actor.id == target_user.id:
                logger.warning(f"{actor.log} Attempted to change their own role")
                raise PermissionDeniedError()

            if target_user.role == Role.OWNER:
                logger.warning(f"{actor.log} Attempted to change role of OWNER '{data.user_id}'")
                raise PermissionDeniedError()

            if not actor.role > data.role:
                logger.warning(
                    f"{actor.log} Attempted to assign role '{data.role}' "
                    f"which is >= their own role '{actor.role}'"
                )
                raise PermissionDeniedError()

            if not actor.role > target_user.role:
                logger.warning(
                    f"{actor.log} Attempted to change role of '{data.user_id}' "
                    f"({target_user.role}) which is >= their own role '{actor.role}'"
                )
                raise PermissionDeniedError()

            target_user.role = data.role
            await self.user_dao.update(target_user)
            await self.uow.commit()

        logger.info(f"{actor.log} Changed role for user '{data.user_id}' to '{data.role.value}'")
