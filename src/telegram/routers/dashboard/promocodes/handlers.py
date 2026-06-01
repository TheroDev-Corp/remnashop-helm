from adaptix import Retort
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import DialogManager, ShowMode
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.kbd import Button
from dishka import FromDishka
from dishka.integrations.aiogram_dialog import inject
from loguru import logger

from src.application.common import Notifier
from src.application.dto import PlanSnapshotDto, PromocodeDto
from src.application.use_cases.promocode.commands.manage import (
    CreatePromocode,
    CreatePromocodeDto,
    DeletePromocode,
    UpdatePromocode,
)
from src.application.use_cases.promocode.queries.get import GetPromocode
from src.application.use_cases.user.queries.plans import GetAvailablePlans
from src.core.constants import USER_KEY
from src.core.enums import PromocodeAvailability, PromocodeRewardType
from src.telegram.states import DashboardPromocodes
from src.telegram.utils import is_double_click

PROMO_PLAN_ID_KEY = "promo_plan_id"


def is_promo_complete(promo: PromocodeDto) -> bool:
    """A draft promocode is ready to create/save when its reward is set.

    SUBSCRIPTION rewards carry a ``plan_snapshot`` instead of a numeric ``reward``;
    every other reward type requires a numeric ``reward``.
    """
    if not promo.code:
        return False
    if promo.reward_type == PromocodeRewardType.SUBSCRIPTION:
        return promo.plan_snapshot is not None
    return promo.reward is not None


PROMO_PAGE_KEY = "promo_page"
PAGE_SIZE = 10

_PROMO_KEY = PromocodeDto.__name__


def _load(dialog_manager: DialogManager, retort: Retort) -> PromocodeDto:
    return retort.load(dialog_manager.dialog_data[_PROMO_KEY], PromocodeDto)


def _save(dialog_manager: DialogManager, retort: Retort, promo: PromocodeDto) -> None:
    dialog_manager.dialog_data[_PROMO_KEY] = retort.dump(promo)


@inject
async def on_promo_select(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
    get_promocode: FromDishka[GetPromocode],
    retort: FromDishka[Retort],
) -> None:
    user = dialog_manager.middleware_data[USER_KEY]
    promo_id = int(dialog_manager.item_id)  # type: ignore[attr-defined]
    promo = await get_promocode(user, promo_id)
    if not promo:
        return
    dialog_manager.dialog_data[_PROMO_KEY] = retort.dump(promo)
    dialog_manager.dialog_data["is_edit"] = True
    await dialog_manager.switch_to(DashboardPromocodes.CONFIGURATOR)


@inject
async def on_create_promo(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
) -> None:
    dialog_manager.dialog_data.pop(_PROMO_KEY, None)
    dialog_manager.dialog_data["is_edit"] = False
    await dialog_manager.switch_to(DashboardPromocodes.CONFIGURATOR)


@inject
async def on_promo_confirm(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
    notifier: FromDishka[Notifier],
    create_promocode: FromDishka[CreatePromocode],
    update_promocode: FromDishka[UpdatePromocode],
) -> None:
    user = dialog_manager.middleware_data[USER_KEY]
    promo = _load(dialog_manager, retort)
    is_edit: bool = dialog_manager.dialog_data.get("is_edit", False)

    if not is_promo_complete(promo):
        await notifier.notify_user(user, i18n_key="ntf-promocode.fields-required")
        return

    if not is_edit:
        try:
            await create_promocode(
                user,
                CreatePromocodeDto(
                    code=promo.code,
                    reward_type=promo.reward_type,
                    reward=promo.reward,
                    plan_snapshot=promo.plan_snapshot,
                    availability=promo.availability,
                    allowed_telegram_ids=promo.allowed_telegram_ids,
                    lifetime=promo.lifetime,
                    max_activations=promo.max_activations,
                ),
            )
        except ValueError:
            await notifier.notify_user(user, i18n_key="ntf-promocode.code-exists")
            return
        logger.info(f"{user.log} Created promocode '{promo.code}'")
        await notifier.notify_user(user, i18n_key="ntf-promocode.created")
    else:
        await update_promocode(user, promo)
        logger.info(f"{user.log} Updated promocode '{promo.code}'")
        await notifier.notify_user(user, i18n_key="ntf-promocode.updated")

    await dialog_manager.switch_to(DashboardPromocodes.MAIN)


@inject
async def on_toggle_active(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
) -> None:
    promo = _load(dialog_manager, retort)
    promo.is_active = not promo.is_active
    _save(dialog_manager, retort, promo)


@inject
async def on_delete_promo(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
    delete_promocode: FromDishka[DeletePromocode],
    notifier: FromDishka[Notifier],
) -> None:
    if is_double_click(dialog_manager, key="promo_delete"):
        return
    user = dialog_manager.middleware_data[USER_KEY]
    promo = _load(dialog_manager, retort)
    await delete_promocode(user, promo.id)
    dialog_manager.dialog_data.pop(_PROMO_KEY, None)
    await notifier.notify_user(user, i18n_key="ntf-promocode.deleted")
    await dialog_manager.switch_to(DashboardPromocodes.MAIN)


@inject
async def on_code_input(
    message: Message,
    widget: MessageInput,
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
    notifier: FromDishka[Notifier],
) -> None:
    dialog_manager.show_mode = ShowMode.EDIT
    user = dialog_manager.middleware_data[USER_KEY]
    code = (message.text or "").strip()
    if not (3 <= len(code) <= 16):
        await notifier.notify_user(user, i18n_key="ntf-promocode.code-invalid")
        return
    promo = _load(dialog_manager, retort)
    promo.code = code
    _save(dialog_manager, retort, promo)
    await dialog_manager.switch_to(DashboardPromocodes.CONFIGURATOR)


@inject
async def on_type_select(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
) -> None:
    reward_type = PromocodeRewardType(dialog_manager.item_id)  # type: ignore[attr-defined]
    promo = _load(dialog_manager, retort)
    promo.reward_type = reward_type
    if reward_type == PromocodeRewardType.SUBSCRIPTION:
        promo.reward = None
    else:
        promo.plan_snapshot = None
    _save(dialog_manager, retort, promo)
    await dialog_manager.switch_to(DashboardPromocodes.CONFIGURATOR)


@inject
async def on_reward_input(
    message: Message,
    widget: MessageInput,
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
    notifier: FromDishka[Notifier],
) -> None:
    dialog_manager.show_mode = ShowMode.EDIT
    user = dialog_manager.middleware_data[USER_KEY]
    reward_text = (message.text or "").strip()
    if not reward_text.isdigit():
        await notifier.notify_user(user, i18n_key="ntf-promocode.reward-invalid")
        return
    promo = _load(dialog_manager, retort)
    promo.reward = int(reward_text)
    _save(dialog_manager, retort, promo)
    await dialog_manager.switch_to(DashboardPromocodes.CONFIGURATOR)


@inject
async def on_plan_select(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
) -> None:
    plan_id = int(dialog_manager.item_id)  # type: ignore[attr-defined]
    dialog_manager.dialog_data[PROMO_PLAN_ID_KEY] = plan_id
    await dialog_manager.switch_to(DashboardPromocodes.PLAN_DURATION)


@inject
async def on_plan_duration_select(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
    get_available_plans: FromDishka[GetAvailablePlans],
) -> None:
    user = dialog_manager.middleware_data[USER_KEY]
    plan_id = dialog_manager.dialog_data.get(PROMO_PLAN_ID_KEY)
    days = int(dialog_manager.item_id)  # type: ignore[attr-defined]
    plans = await get_available_plans.system(user)
    plan = next((p for p in plans if p.id == plan_id), None)
    if plan is None or plan.get_duration(days) is None:
        return
    snapshot = PlanSnapshotDto.from_plan(plan, days)
    promo = _load(dialog_manager, retort)
    promo.plan_snapshot = retort.dump(snapshot)
    promo.reward = None
    _save(dialog_manager, retort, promo)
    await dialog_manager.switch_to(DashboardPromocodes.CONFIGURATOR)


@inject
async def on_availability_select(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
) -> None:
    availability = PromocodeAvailability(dialog_manager.item_id)  # type: ignore[attr-defined]
    promo = _load(dialog_manager, retort)
    promo.availability = availability
    _save(dialog_manager, retort, promo)
    await dialog_manager.switch_to(DashboardPromocodes.CONFIGURATOR)


@inject
async def on_allowed_id_input(
    message: Message,
    widget: MessageInput,
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
    notifier: FromDishka[Notifier],
) -> None:
    dialog_manager.show_mode = ShowMode.EDIT
    user = dialog_manager.middleware_data[USER_KEY]
    id_text = (message.text or "").strip()
    if not id_text.isdigit():
        await notifier.notify_user(user, i18n_key="ntf-promocode.reward-invalid")
        return
    promo = _load(dialog_manager, retort)
    telegram_id = int(id_text)
    if telegram_id not in promo.allowed_telegram_ids:
        promo.allowed_telegram_ids = [*promo.allowed_telegram_ids, telegram_id]
    _save(dialog_manager, retort, promo)


@inject
async def on_allowed_id_remove(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
) -> None:
    promo = _load(dialog_manager, retort)
    promo.allowed_telegram_ids = [
        i
        for i in promo.allowed_telegram_ids
        if str(i) != str(dialog_manager.item_id)  # type: ignore[attr-defined]
    ]
    _save(dialog_manager, retort, promo)


@inject
async def on_lifetime_input(
    message: Message,
    widget: MessageInput,
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
    notifier: FromDishka[Notifier],
) -> None:
    dialog_manager.show_mode = ShowMode.EDIT
    user = dialog_manager.middleware_data[USER_KEY]
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await notifier.notify_user(user, i18n_key="ntf-promocode.reward-invalid")
        return
    promo = _load(dialog_manager, retort)
    promo.lifetime = int(text)
    _save(dialog_manager, retort, promo)
    await dialog_manager.switch_to(DashboardPromocodes.CONFIGURATOR)


@inject
async def on_lifetime_reset(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
) -> None:
    promo = _load(dialog_manager, retort)
    promo.lifetime = None
    _save(dialog_manager, retort, promo)
    await dialog_manager.switch_to(DashboardPromocodes.CONFIGURATOR)


@inject
async def on_max_activations_input(
    message: Message,
    widget: MessageInput,
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
    notifier: FromDishka[Notifier],
) -> None:
    dialog_manager.show_mode = ShowMode.EDIT
    user = dialog_manager.middleware_data[USER_KEY]
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await notifier.notify_user(user, i18n_key="ntf-promocode.reward-invalid")
        return
    promo = _load(dialog_manager, retort)
    promo.max_activations = int(text)
    _save(dialog_manager, retort, promo)
    await dialog_manager.switch_to(DashboardPromocodes.CONFIGURATOR)


@inject
async def on_max_activations_reset(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
) -> None:
    promo = _load(dialog_manager, retort)
    promo.max_activations = None
    _save(dialog_manager, retort, promo)
    await dialog_manager.switch_to(DashboardPromocodes.CONFIGURATOR)


@inject
async def on_page_next(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
) -> None:
    page = dialog_manager.dialog_data.get(PROMO_PAGE_KEY, 0)
    dialog_manager.dialog_data[PROMO_PAGE_KEY] = page + 1


@inject
async def on_page_prev(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
) -> None:
    page = dialog_manager.dialog_data.get(PROMO_PAGE_KEY, 0)
    dialog_manager.dialog_data[PROMO_PAGE_KEY] = max(0, page - 1)
