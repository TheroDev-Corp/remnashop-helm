from dataclasses import dataclass
from datetime import timedelta

from loguru import logger

from src.application.common import Interactor
from src.application.common.dao import PromocodeDao, SubscriptionDao, UserDao
from src.application.common.policy import Permission
from src.application.dto import PromocodeDto, UserDto
from src.core.enums import PromocodeAvailability, PromocodeRewardType
from src.core.exceptions import (
    PromocodeAlreadyActivatedError,
    PromocodeNotAvailableError,
    PromocodeNotFoundError,
)
from src.core.utils.time import datetime_now

SUBSCRIPTION_REQUIRED_REWARDS = frozenset(
    {
        PromocodeRewardType.DURATION,
        PromocodeRewardType.TRAFFIC,
        PromocodeRewardType.DEVICES,
    }
)


@dataclass(frozen=True)
class ValidatePromocodeDto:
    code: str
    user: UserDto


class ValidatePromocode(Interactor[ValidatePromocodeDto, PromocodeDto]):
    required_permission = Permission.PUBLIC

    def __init__(
        self,
        promocode_dao: PromocodeDao,
        user_dao: UserDao,
        subscription_dao: SubscriptionDao,
    ) -> None:
        self.promocode_dao = promocode_dao
        self.user_dao = user_dao
        self.subscription_dao = subscription_dao

    async def _execute(self, actor: UserDto, data: ValidatePromocodeDto) -> PromocodeDto:
        user = data.user
        code = data.code.strip().upper()

        promo = await self.promocode_dao.get_by_code(code)

        if not promo or not promo.is_active:
            logger.info(f"{actor.log} Promocode '{code}' not found or inactive")
            raise PromocodeNotFoundError(f"Promocode '{code}' not found")

        assert promo.id is not None

        if promo.lifetime is not None and promo.created_at is not None:
            expires_at = promo.created_at + timedelta(days=promo.lifetime)
            if datetime_now() > expires_at:
                logger.info(f"{actor.log} Promocode '{code}' expired")
                raise PromocodeNotAvailableError("Promocode has expired")

        if promo.max_activations is not None:
            count = await self.promocode_dao.get_activations_count(promo.id)
            if count >= promo.max_activations:
                logger.info(f"{actor.log} Promocode '{code}' max activations reached")
                raise PromocodeNotAvailableError("Promocode activation limit reached")

        existing = await self.promocode_dao.get_activation_by_user(promo.id, user.id)
        if existing:
            logger.info(f"{actor.log} Promocode '{code}' already activated by user")
            raise PromocodeAlreadyActivatedError("Promocode already activated")

        if promo.reward_type in SUBSCRIPTION_REQUIRED_REWARDS:
            current = await self.subscription_dao.get_current(user.id)
            if current is None:
                logger.info(f"{actor.log} Promocode '{code}' requires an active subscription")
                raise PromocodeNotAvailableError("Active subscription required for this promocode")

        await self._check_availability(actor, user, promo)

        logger.info(f"{actor.log} Promocode '{code}' is valid for user")
        return promo

    async def _check_availability(self, actor: UserDto, user: UserDto, promo: PromocodeDto) -> None:
        match promo.availability:
            case PromocodeAvailability.ALL:
                return

            case PromocodeAvailability.NEW:
                has_sub = await self.user_dao.has_any_subscription(user.id, include_trial=False)
                if has_sub:
                    raise PromocodeNotAvailableError("Promocode is for new users only")

            case PromocodeAvailability.EXISTING:
                has_sub = await self.user_dao.has_any_subscription(user.id, include_trial=False)
                if not has_sub:
                    raise PromocodeNotAvailableError("Promocode is for existing users only")

            case PromocodeAvailability.INVITED:
                is_invited = await self.user_dao.is_invited_user(user.id)
                if not is_invited:
                    raise PromocodeNotAvailableError("Promocode is for invited users only")

            case PromocodeAvailability.ALLOWED:
                allowed = promo.allowed_telegram_ids
                if user.telegram_id is None or user.telegram_id not in allowed:
                    raise PromocodeNotAvailableError("User not in allowed list")
