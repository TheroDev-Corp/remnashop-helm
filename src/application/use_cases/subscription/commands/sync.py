from loguru import logger

from src.application.common import Interactor, Remnawave
from src.application.common.dao import SubscriptionDao, UserDao
from src.application.common.policy import Permission
from src.application.common.uow import UnitOfWork
from src.application.dto import RemnaSubscriptionDto, UserDto
from src.application.use_cases.remnawave.commands.synchronization import (
    SyncRemnaUser,
    bind_subscription_to_remna_user,
)
from src.application.use_cases.subscription.queries.match import (
    MatchSubscription,
    MatchSubscriptionDto,
)
from src.core.enums import SubscriptionStatus


class CheckSubscriptionSyncState(Interactor[int, bool]):
    required_permission = Permission.USER_SYNC

    def __init__(
        self,
        uow: UnitOfWork,
        user_dao: UserDao,
        subscription_dao: SubscriptionDao,
        remnawave: Remnawave,
        match_subscription: MatchSubscription,
    ) -> None:
        self.uow = uow
        self.user_dao = user_dao
        self.subscription_dao = subscription_dao
        self.remnawave = remnawave
        self.match_subscription = match_subscription

    async def _execute(self, actor: UserDto, user_id: int) -> bool:
        target_user = await self.user_dao.get_by_id(user_id)
        if not target_user:
            raise ValueError(f"User '{user_id}' not found")

        bot_sub = await self.subscription_dao.get_current(target_user.id)
        remna_user = await self.remnawave.resolve_user(
            target_user,
            bot_sub.user_remna_id if bot_sub else None,
        )

        remna_sub = RemnaSubscriptionDto.from_remna_user(remna_user) if remna_user else None

        if not remna_sub and not bot_sub:
            raise ValueError(f"{actor.log} No subscription data found to check for '{user_id}'")

        if await self.match_subscription.system(MatchSubscriptionDto(bot_sub, remna_sub)):
            logger.info(f"{actor.log} Subscription data for user '{user_id}' is consistent")
            return False

        logger.info(f"{actor.log} Inconsistency detected for user '{user_id}'")
        return True


class SyncSubscriptionFromRemnawave(Interactor[int, None]):
    required_permission = Permission.USER_SYNC

    def __init__(
        self,
        uow: UnitOfWork,
        user_dao: UserDao,
        subscription_dao: SubscriptionDao,
        remnawave: Remnawave,
        sync_remna_user: SyncRemnaUser,
    ) -> None:
        self.uow = uow
        self.user_dao = user_dao
        self.subscription_dao = subscription_dao
        self.remnawave = remnawave
        self.sync_remna_user = sync_remna_user

    async def _execute(self, actor: UserDto, user_id: int) -> None:
        async with self.uow:
            target_user = await self.user_dao.get_by_id(user_id)
            if not target_user:
                raise ValueError(f"User '{user_id}' not found")

            subscription = await self.subscription_dao.get_current(target_user.id)

            # Panel/transport errors propagate from resolve_user: only a verified "no panel user
            # owned by this user" may lead to deleting the local subscription.
            remna_user = await self.remnawave.resolve_user(
                target_user,
                subscription.user_remna_id if subscription else None,
            )

            if not remna_user:
                if subscription:
                    await self.subscription_dao.update_status(
                        subscription.id,
                        SubscriptionStatus.DELETED,
                    )
                    await self.user_dao.clear_current_subscription(target_user.id)
                    logger.info(
                        f"{actor.log} Deleted subscription for user '{user_id}' "
                        f"because it missing in Remnawave"
                    )
                else:
                    logger.info(f"{actor.log} No subscription to sync for user '{user_id}'")
                await self.uow.commit()
                return

            remna_subscription = RemnaSubscriptionDto.from_remna_user(remna_user)

            if not subscription:
                logger.info(f"{actor.log} Importing subscription from panel for user '{user_id}'")
                await self.sync_remna_user._import_subscription(target_user.id, remna_subscription)
            else:
                # apply_sync copies the (resolve_user-verified) panel id into user_remna_id.
                await self.sync_remna_user._update_subscription(subscription, remna_subscription)

            await self.uow.commit()
            logger.info(f"{actor.log} Synchronized subscription from remnapy for user '{user_id}'")


class SyncSubscriptionFromRemnashop(Interactor[int, None]):
    required_permission = Permission.USER_SYNC

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

    async def _execute(self, actor: UserDto, user_id: int) -> None:
        async with self.uow:
            target_user = await self.user_dao.get_by_id(user_id)
            if not target_user:
                raise ValueError(f"User '{user_id}' not found")

            subscription = await self.subscription_dao.get_current(target_user.id)

            if not subscription:
                remna_user = await self.remnawave.resolve_user(target_user)
                if remna_user:
                    await self.remnawave.delete_user(remna_user.id)
                    logger.info(
                        f"{actor.log} Deleted user '{remna_user.id}' from remnapy "
                        f"due to missing local subscription"
                    )
            else:
                remna_user = await self.remnawave.resolve_user(
                    target_user,
                    subscription.user_remna_id,
                )

                if remna_user:
                    # Check the binding before mutating the panel, not after.
                    await self.subscription_dao.ensure_remna_id_available(
                        remna_user.id, target_user.id
                    )
                    result = await self.remnawave.update_user(
                        user=target_user,
                        id=remna_user.id,
                        subscription=subscription,
                    )
                    logger.info(
                        f"{actor.log} Updated user '{user_id}' in Remnawave with local data"
                    )
                else:
                    result = await self.remnawave.create_user(
                        user=target_user,
                        subscription=subscription,
                    )
                    logger.info(
                        f"{actor.log} Recreated user '{user_id}' in Remnawave with local data"
                    )

                # Bind to the panel user returned for *this* bot user (update_user may re-resolve).
                if bind_subscription_to_remna_user(subscription, result):
                    await self.subscription_dao.update(subscription)

            await self.uow.commit()
