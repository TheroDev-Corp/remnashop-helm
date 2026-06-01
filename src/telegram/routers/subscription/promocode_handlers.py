from typing import Any, cast

from aiogram.types import CallbackQuery, Message
from aiogram_dialog import DialogManager, ShowMode
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.kbd import Button
from dishka import FromDishka
from dishka.integrations.aiogram_dialog import inject
from loguru import logger

from src.application.common import Notifier
from src.application.dto import TelegramUserDto
from src.application.use_cases.promocode.commands.activate import (
    ActivatePromocode,
    ActivatePromocodeDto,
)
from src.application.use_cases.promocode.queries.validate import (
    ValidatePromocode,
    ValidatePromocodeDto,
)
from src.core.constants import USER_KEY
from src.core.exceptions import (
    PromocodeAlreadyActivatedError,
    PromocodeNotAvailableError,
    PromocodeNotFoundError,
)
from src.telegram.states import Subscription
from src.telegram.utils import is_double_click

PENDING_PROMO_KEY = "pending_promo_code"
PENDING_PROMO_DTO_KEY = "pending_promo_dto"


@inject
async def on_promocode_input(
    message: Message,
    widget: MessageInput,
    dialog_manager: DialogManager,
    validate_promocode: FromDishka[ValidatePromocode],
    notifier: FromDishka[Notifier],
) -> None:
    dialog_manager.show_mode = ShowMode.EDIT
    user: TelegramUserDto = dialog_manager.middleware_data[USER_KEY]
    code = (message.text or "").strip().upper()

    if not code:
        return

    try:
        promo = await validate_promocode(user, ValidatePromocodeDto(code=code, user=user))
    except PromocodeNotFoundError:
        await notifier.notify_user(user, i18n_key="ntf-promocode.not-found")
        return
    except PromocodeAlreadyActivatedError:
        await notifier.notify_user(user, i18n_key="ntf-promocode.already-activated")
        return
    except PromocodeNotAvailableError:
        await notifier.notify_user(user, i18n_key="ntf-promocode.not-available")
        return

    logger.info(f"{user.log} Promocode '{code}' validated, pending confirmation")

    dialog_manager.dialog_data[PENDING_PROMO_KEY] = promo.code
    dialog_manager.dialog_data[PENDING_PROMO_DTO_KEY] = {
        "code": promo.code,
        "reward_type": promo.reward_type.value,
        "reward": promo.reward,
    }


@inject
async def on_promocode_confirm(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
    activate_promocode: FromDishka[ActivatePromocode],
    notifier: FromDishka[Notifier],
) -> None:
    if is_double_click(dialog_manager, key="promo_confirm"):
        return

    user: TelegramUserDto = dialog_manager.middleware_data[USER_KEY]
    code = dialog_manager.dialog_data.get(PENDING_PROMO_KEY)

    if not code:
        return

    try:
        promo = await activate_promocode(user, ActivatePromocodeDto(code=code, user=user))
    except PromocodeAlreadyActivatedError:
        await notifier.notify_user(user, i18n_key="ntf-promocode.already-activated")
        return
    except (PromocodeNotFoundError, PromocodeNotAvailableError):
        await notifier.notify_user(user, i18n_key="ntf-promocode.activation-failed")
        return

    logger.info(f"{user.log} Activated promocode '{promo.code}'")

    dialog_manager.dialog_data.pop(PENDING_PROMO_KEY, None)
    dialog_manager.dialog_data.pop(PENDING_PROMO_DTO_KEY, None)
    await notifier.notify_user(user, i18n_key="ntf-promocode.activated")
    await dialog_manager.switch_to(Subscription.MAIN)


async def getter_promocode(dialog_manager: DialogManager, **kwargs: Any) -> dict[str, Any]:
    if dialog_manager.start_data and not dialog_manager.dialog_data.get(PENDING_PROMO_KEY):
        start_data = cast(dict[str, Any], dialog_manager.start_data)
        prefill = start_data.get("prefill_code")
        if prefill:
            dialog_manager.dialog_data[PENDING_PROMO_KEY] = prefill

    promo_data: dict[str, Any] = cast(
        dict[str, Any], dialog_manager.dialog_data.get(PENDING_PROMO_DTO_KEY, {})
    )
    return {
        "has_promo": bool(promo_data),
        "promo_code": promo_data.get("code", dialog_manager.dialog_data.get(PENDING_PROMO_KEY, "")),
        "promo_reward_type": promo_data.get("reward_type", ""),
        "promo_reward": promo_data.get("reward", 0),
    }
