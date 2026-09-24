from datetime import timedelta

from loguru import logger
from redis.asyncio import Redis

from src.application.common import EventPublisher
from src.application.common.dao import SettingsDao, SubscriptionDao, UserDao
from src.application.dto import SubscriptionDto, UserDto
from src.application.events import SubscriptionExpiresEvent
from src.core.constants import TIME_1D
from src.core.enums import UserNotificationType
from src.core.utils.time import datetime_now


def expiry_notification_type(day: int) -> UserNotificationType:
    # Admin toggles exist for 3/2/1 days; any longer custom reminder falls under the 3-day one.
    if day <= 1:
        return UserNotificationType.EXPIRES_IN_1_DAY
    if day == 2:
        return UserNotificationType.EXPIRES_IN_2_DAYS
    return UserNotificationType.EXPIRES_IN_3_DAYS


class ExpiryReminderService:
    """Sends "subscription expires in N days" reminders.

    Two sources feed it: Remnawave `user.expires_in_*` webhooks and an hourly DB scan
    (fallback for panels that never deliver those webhooks). A Redis key per
    subscription + expiry date + day makes sure the user gets each reminder once,
    whichever source comes first; renewing changes `expire_at` and re-arms them.
    """

    def __init__(
        self,
        settings_dao: SettingsDao,
        subscription_dao: SubscriptionDao,
        user_dao: UserDao,
        event_bus: EventPublisher,
        redis: Redis,
    ) -> None:
        self.settings_dao = settings_dao
        self.subscription_dao = subscription_dao
        self.user_dao = user_dao
        self.event_bus = event_bus
        self.redis = redis

    async def remind(self, user: UserDto, subscription: SubscriptionDto, day: int) -> bool:
        settings = await self.settings_dao.get()
        if day not in settings.notifications.expiry_reminder.days:
            logger.debug(f"Expiry reminder for {day} day(s) is not configured, skipping")
            return False
        return await self._send(user, subscription, day)

    async def check_expiring(self) -> int:
        settings = await self.settings_dao.get()
        config = settings.notifications.expiry_reminder
        if not config.fallback_enabled or not config.days:
            return 0

        now = datetime_now()
        subscriptions = await self.subscription_dao.get_expiring_current(
            now + timedelta(days=max(config.days))
        )
        if not subscriptions:
            return 0

        users = {
            user.id: user
            for user in await self.user_dao.get_by_ids([s.user_id for s in subscriptions])
        }
        days = sorted(config.days)
        sent = 0

        for subscription in subscriptions:
            user = users.get(subscription.user_id)
            if not user:
                continue
            remaining = subscription.expire_at - now
            # Only the nearest crossed threshold: after downtime the user gets one
            # up-to-date reminder instead of a burst of stale ones.
            day = next((d for d in days if remaining <= timedelta(days=d)), None)
            if day is not None and await self._send(user, subscription, day):
                sent += 1

        if sent:
            logger.info(f"Sent '{sent}' subscription expiry reminders")
        return sent

    async def _send(self, user: UserDto, subscription: SubscriptionDto, day: int) -> bool:
        expire_at = subscription.expire_at
        key = f"expiry_reminder:{subscription.id}:{int(expire_at.timestamp())}:{day}"
        ttl = max(int((expire_at - datetime_now()).total_seconds()), 0) + TIME_1D

        if not await self.redis.set(key, 1, nx=True, ex=ttl):
            logger.debug(f"Expiry reminder '{key}' already sent, skipping")
            return False

        await self.event_bus.publish(
            SubscriptionExpiresEvent(
                user=user,
                day=day,
                is_trial=subscription.is_trial,
                notification_type=expiry_notification_type(day),
            )
        )
        return True
