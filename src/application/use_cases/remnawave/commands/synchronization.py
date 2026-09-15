from dataclasses import dataclass
from typing import Optional

from loguru import logger

from src.application.common import Cryptographer, Interactor, Remnawave
from src.application.common.dao import SubscriptionDao, UserDao
from src.application.common.policy import Permission
from src.application.common.uow import UnitOfWork
from src.application.dto import PlanSnapshotDto, RemnaSubscriptionDto, SubscriptionDto, UserDto
from src.core.config import AppConfig
from src.core.constants import IMPORTED_TAG
from src.core.enums import Role, SubscriptionStatus
from src.core.types import RemnaUserDto
from src.core.utils.converters import limits_to_plan_type
from src.core.utils.time import datetime_now


@dataclass(frozen=True)
class SyncRemnaUserDto:
    remna_user: RemnaUserDto
    creating: bool


def bind_subscription_to_remna_user(
    subscription: SubscriptionDto,
    remna_user: RemnaUserDto,
) -> bool:
    """Point `subscription` to `remna_user`, which the caller MUST have obtained for the
    subscription's owner (resolve_user / create_user / update_user). Returns True if changed."""
    if subscription.user_remna_id != remna_user.id:
        logger.info(
            f"Binding subscription '{subscription.id}' to RemnaUser "
            f"'{subscription.user_remna_id}' -> '{remna_user.id}'"
        )
        subscription.user_remna_id = remna_user.id

    subscription_url = getattr(remna_user, "subscription_url", None)
    if subscription_url and subscription.url != subscription_url:
        subscription.url = subscription_url

    return bool(subscription.changed_data)


async def find_bot_user_for_remna_user(
    user_dao: UserDao,
    remnawave: Remnawave,
    remna_user: RemnaUserDto,
) -> tuple[Optional[UserDto], bool]:
    """Find the bot user owning `remna_user`.

    Returns `(user, conflict)`. `conflict` is True when a bot user is bound to this panel user
    in the DB but does not own it (so the caller must neither bind nor create anything).
    """
    if remna_user.telegram_id:
        # telegramId is authoritative: never fall back to the stored remna id.
        return await user_dao.get_by_telegram_id(remna_user.telegram_id), False

    remna_id = getattr(remna_user, "id", None)
    if not remna_id:
        return None, False

    # get_by_remna_id matches the user's *current* subscription, so remna_id is its stored id.
    user = await user_dao.get_by_remna_id(remna_id)
    if user and not remnawave.is_owned_by(remna_user, user, remna_id):  # type: ignore[arg-type]
        logger.warning(
            f"RemnaUser '{remna_id}' (no telegram_id, username '{remna_user.username}') is bound "
            f"to {user.log} in DB but not owned by them, refusing to match"
        )
        return None, True

    return user, False


class SyncRemnaUser(Interactor[SyncRemnaUserDto, bool]):
    required_permission = Permission.USER_SYNC

    def __init__(
        self,
        uow: UnitOfWork,
        user_dao: UserDao,
        subscription_dao: SubscriptionDao,
        config: AppConfig,
        remnawave: Remnawave,
        cryptographer: Cryptographer,
    ) -> None:
        self.uow = uow
        self.user_dao = user_dao
        self.subscription_dao = subscription_dao
        self.config = config
        self.remnawave = remnawave
        self.cryptographer = cryptographer

    async def _execute(self, actor: UserDto, data: SyncRemnaUserDto) -> bool:  # noqa: C901
        remna_user = data.remna_user
        user_identifier = getattr(remna_user, "uuid", None) or getattr(remna_user, "id", "unknown")

        async with self.uow:
            user, conflict = await find_bot_user_for_remna_user(
                self.user_dao, self.remnawave, remna_user
            )
            if conflict:
                return False

            created = False
            if not user and data.creating:
                logger.debug(f"User '{user_identifier}' not found in bot, creating new user")

                async def persist(referral_code: str) -> UserDto:
                    return await self.user_dao.create(
                        self._create_user_dto(data.remna_user, referral_code)
                    )

                user = await self.uow.persist_with_unique_code(
                    generate=lambda: self.cryptographer.generate_unique_code(
                        self.user_dao.get_by_referral_code
                    ),
                    persist=persist,
                    column="referral_code",
                )
                created = True

            if not user:
                logger.warning(
                    f"Sync failed: user '{user_identifier}' could not be found or created"
                )
                return False

            subscription = await self.subscription_dao.get_current(user.id)

            # A bot user just created for this panel user owns it by construction: no bot user
            # was bound to it (checked above) and the DB guard rejects a concurrent binding.
            stored_remna_id = subscription.user_remna_id if subscription else None
            if not created and not self.remnawave.is_owned_by(
                remna_user,  # type: ignore[arg-type]
                user,
                stored_remna_id,
            ):
                logger.warning(
                    f"Sync refused: RemnaUser '{user_identifier}' is not owned by {user.log}"
                )
                return False
            remna_subscription = RemnaSubscriptionDto.from_remna_user(remna_user)

            if not subscription:
                logger.info(
                    f"No subscription found for user '{user.remna_name}', importing from panel"
                )
                await self._import_subscription(user.id, remna_subscription)
                await self.uow.commit()
                target_id = remna_user.telegram_id or user_identifier
                logger.info(f"Sync completed for user '{target_id}'")
                return False

            if 0 < subscription.user_remna_id != remna_user.id:
                # The user may own several panel users (duplicates). Keep a still-valid binding
                # instead of flipping it to whichever duplicate triggered the sync.
                stored = await self.remnawave.get_user_by_id(subscription.user_remna_id)
                if stored and self.remnawave.is_owned_by(stored, user, subscription.user_remna_id):
                    logger.warning(
                        f"{user.log} owns several RemnaUsers: current subscription is bound to "
                        f"'{stored.id}', skipping sync from '{remna_user.id}'"
                    )
                    return False

            logger.info(f"Synchronizing existing subscription for user {user.log}")
            changed = await self._update_subscription(subscription, remna_subscription)
            await self.uow.commit()
            target_id = remna_user.telegram_id or user_identifier
            logger.info(f"Sync completed for user '{target_id}'")
            return changed

    def _create_user_dto(self, data: RemnaUserDto, referral_code: str) -> UserDto:
        fallback_name = str(getattr(data, "uuid", None) or getattr(data, "id", "user"))
        return UserDto(
            telegram_id=data.telegram_id,
            referral_code=referral_code,
            name=str(data.telegram_id) if data.telegram_id else fallback_name,
            role=Role.USER,
            language=self.config.default_locale,
        )

    async def _import_subscription(
        self,
        user_id: int,
        remna_subscription: RemnaSubscriptionDto,
    ) -> None:
        plan = PlanSnapshotDto(
            id=-1,
            name=IMPORTED_TAG,
            tag=remna_subscription.tag,
            type=limits_to_plan_type(
                remna_subscription.traffic_limit,
                remna_subscription.device_limit,
            ),
            traffic_limit=remna_subscription.traffic_limit,
            device_limit=remna_subscription.device_limit,
            duration=0,
            traffic_limit_strategy=remna_subscription.traffic_limit_strategy,
            internal_squads=remna_subscription.internal_squads,
            external_squad=remna_subscription.external_squad,
        )

        expired = remna_subscription.expire_at and remna_subscription.expire_at < datetime_now()
        status = (
            SubscriptionStatus.EXPIRED if expired else SubscriptionStatus(remna_subscription.status)
        )

        subscription = SubscriptionDto(
            user_remna_id=remna_subscription.id,
            status=status,
            traffic_limit=plan.traffic_limit,
            device_limit=plan.device_limit,
            traffic_limit_strategy=plan.traffic_limit_strategy,
            tag=plan.tag,
            internal_squads=remna_subscription.internal_squads,
            external_squad=remna_subscription.external_squad,
            expire_at=remna_subscription.expire_at,
            url=remna_subscription.url,
            plan_snapshot=plan,
        )

        await self.subscription_dao.create(subscription, user_id)

    async def _update_subscription(
        self,
        target: SubscriptionDto,
        source: RemnaSubscriptionDto,
    ) -> bool:
        # Callers must have verified that `source` belongs to the subscription's owner:
        # apply_sync copies the panel id into `user_remna_id`.
        subscription = self.remnawave.apply_sync(target, source)
        await self.subscription_dao.update(subscription)
        return bool(subscription.changed_data)


class SyncAllUsersFromBot(Interactor[None, dict[str, int]]):
    required_permission = None

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

    async def _execute(self, actor: UserDto, data: None) -> dict[str, int]:
        bot_users = await self._fetch_all_bot_users()

        logger.info(f"Total users in bot for reverse sync: '{len(bot_users)}'")

        updated = 0
        recreated = 0
        skipped = 0
        errors = 0

        for user in bot_users:
            try:
                subscription = await self.subscription_dao.get_current(user.id)

                if not subscription:
                    skipped += 1
                    continue

                remna_user = await self.remnawave.resolve_user(user, subscription.user_remna_id)

                if remna_user:
                    # Check the binding before mutating the panel, not after.
                    await self.subscription_dao.ensure_remna_id_available(remna_user.id, user.id)
                    result = await self.remnawave.update_user(
                        user=user,
                        id=remna_user.id,
                        subscription=subscription,
                    )
                    updated += 1
                else:
                    result = await self.remnawave.create_user(
                        user=user,
                        subscription=subscription,
                    )
                    recreated += 1

                # Persist the id returned for *this* bot user directly (no re-lookup that could
                # match a different bot user).
                if bind_subscription_to_remna_user(subscription, result):
                    async with self.uow:
                        await self.subscription_dao.update(subscription)
                        await self.uow.commit()

            except Exception as exception:
                logger.exception(f"Error reverse-syncing bot user {user.log}: {exception}")
                errors += 1

        result_summary = {
            "total_bot_users": len(bot_users),
            "updated": updated,
            "recreated": recreated,
            "skipped_no_subscription": skipped,
            "errors": errors,
        }

        logger.info(f"Reverse sync (bot → panel) summary: '{result_summary}'")
        return result_summary

    async def _fetch_all_bot_users(self) -> list[UserDto]:
        all_users: list[UserDto] = []
        limit = 500
        offset = 0

        while True:
            batch = await self.user_dao.get_all(limit=limit, offset=offset)
            if not batch:
                break
            all_users.extend(batch)
            if len(batch) < limit:
                break
            offset += len(batch)

        return all_users


class SyncAllUsersFromPanel(Interactor[None, dict[str, int]]):
    required_permission = None

    def __init__(
        self,
        remnawave: Remnawave,
        user_dao: UserDao,
        subscription_dao: SubscriptionDao,
        sync_remna_user: SyncRemnaUser,
    ) -> None:
        self.remnawave = remnawave
        self.user_dao = user_dao
        self.subscription_dao = subscription_dao
        self.sync_remna_user = sync_remna_user

    async def _execute(self, actor: UserDto, data: None) -> dict[str, int]:
        panel_users = await self._fetch_all_panel_users()
        bot_users = await self._fetch_all_bot_users()
        # Web/imported users have no telegram_id; they must not collapse under a `None` key.
        bot_users_map = {user.telegram_id: user for user in bot_users if user.telegram_id}

        logger.info(f"Total users in panel: '{len(panel_users)}'")
        logger.info(f"Total users in bot: '{len(bot_users)}'")

        added_users = 0
        added_subscription = 0
        updated = 0
        errors = 0

        for remna_user in panel_users:
            user_identifier = getattr(remna_user, "uuid", None) or getattr(
                remna_user, "id", "unknown"
            )
            try:
                if remna_user.telegram_id:
                    user = bot_users_map.get(remna_user.telegram_id)
                else:
                    user, conflict = await find_bot_user_for_remna_user(
                        self.user_dao, self.remnawave, remna_user
                    )
                    if conflict:
                        continue

                if not user:
                    await self.sync_remna_user.system(SyncRemnaUserDto(remna_user, True))
                    added_users += 1
                else:
                    current_subscription = await self.subscription_dao.get_current(user.id)
                    if not current_subscription:
                        await self.sync_remna_user.system(SyncRemnaUserDto(remna_user, True))
                        added_subscription += 1
                    else:
                        changed = await self.sync_remna_user.system(
                            SyncRemnaUserDto(remna_user, True)
                        )
                        if changed:
                            updated += 1

            except Exception as exception:
                logger.exception(
                    f"Error syncing RemnaUser '{user_identifier}' exception: {exception}"
                )
                errors += 1

        result = {
            "total_panel_users": len(panel_users),
            "total_bot_users": len(bot_users),
            "added_users": added_users,
            "added_subscription": added_subscription,
            "updated": updated,
            "errors": errors,
        }

        logger.info(f"Sync users summary: '{result}'")
        return result

    async def _fetch_all_panel_users(self) -> list[RemnaUserDto]:
        all_users: list[RemnaUserDto] = []
        limit = 50
        offset = 0

        while True:
            batch = await self.remnawave.get_all_users(limit=limit, offset=offset)
            if not batch:
                break
            all_users.extend(batch)
            if len(batch) < limit:
                break
            offset += len(batch)

        return all_users

    async def _fetch_all_bot_users(self) -> list[UserDto]:
        all_users: list[UserDto] = []
        limit = 500
        offset = 0

        while True:
            batch = await self.user_dao.get_all(limit=limit, offset=offset)
            if not batch:
                break
            all_users.extend(batch)
            if len(batch) < limit:
                break
            offset += len(batch)

        return all_users
