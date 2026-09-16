from dataclasses import dataclass

from loguru import logger

from src.application.common import Interactor, Remnawave
from src.application.common.dao import SubscriptionDao, UserDao
from src.application.common.policy import Permission
from src.application.common.uow import UnitOfWork
from src.application.dto import UserDto
from src.core.enums import SubscriptionStatus
from src.core.exceptions import PermissionDeniedError, UserNotFoundError


@dataclass(frozen=True)
class DeleteUserDto:
    user_id: int


class DeleteUser(Interactor[DeleteUserDto, None]):
    required_permission = Permission.USER_EDITOR

    def __init__(
        self,
        uow: UnitOfWork,
        user_dao: UserDao,
        subscription_dao: SubscriptionDao,
        remnawave: Remnawave,
    ) -> None:
        self.uow = uow
        self.user_dao = user_dao
        self.subscription_dao = subscription_dao
        self.remnawave = remnawave

    async def _execute(self, actor: UserDto, data: DeleteUserDto) -> None:
        async with self.uow:
            target_user = await self.user_dao.get_by_id(data.user_id)
            if not target_user:
                raise UserNotFoundError(f"User '{data.user_id}' not found")

            if not actor.role > target_user.role:
                raise PermissionDeniedError("Cannot delete user with equal or higher role")

            subscription = await self.subscription_dao.get_current(target_user.id)

            if subscription or target_user.telegram_id:
                # Only ever delete a panel user verified to belong to target_user.
                stored_id = subscription.user_remna_id if subscription else None
                try:
                    remna_user = await self.remnawave.resolve_user(target_user, stored_id)
                    if remna_user is None:
                        logger.warning(
                            f"No RemnaUser owned by {target_user.log} found "
                            f"(stored ID '{stored_id}'), skipping panel deletion"
                        )
                    else:
                        await self.remnawave.delete_user(remna_user.id)
                except Exception as e:
                    logger.warning(
                        f"Failed to delete RemnaUser (stored ID '{stored_id}') "
                        f"for user '{data.user_id}': {e}"
                    )

            if subscription:
                await self.subscription_dao.update_status(
                    subscription.id, SubscriptionStatus.DELETED
                )

            await self.user_dao.delete(target_user.id)
            await self.uow.commit()

        logger.info(
            f"{actor.log} Deleted user '{data.user_id}' (Telegram ID: '{target_user.telegram_id}')"
        )
