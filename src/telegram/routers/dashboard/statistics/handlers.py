from typing import Optional

from aiogram.types import CallbackQuery
from aiogram_dialog import DialogManager
from aiogram_dialog.widgets.common import ManagedScroll
from aiogram_dialog.widgets.kbd import Button, Select

from src.telegram.states import DashboardStatistics

from .getters import PROMO_STAT_ID_KEY, PROMO_STAT_PAGE_KEY


async def on_gateway_select(
    callback: CallbackQuery,
    widget: Select,
    dialog_manager: DialogManager,
    selected_page: int,
) -> None:
    widget_scroll: Optional[ManagedScroll] = dialog_manager.find("scroll_transactions")

    if not widget_scroll:
        raise ValueError("scroll_transactions widget not found")

    await widget_scroll.set_page(selected_page)


async def on_plan_select(
    callback: CallbackQuery,
    widget: Select,
    dialog_manager: DialogManager,
    selected_page: int,
) -> None:
    widget_scroll: Optional[ManagedScroll] = dialog_manager.find("scroll_subscriptions")

    if not widget_scroll:
        raise ValueError("scroll_subscriptions widget not found")

    await widget_scroll.set_page(selected_page)


async def on_promo_stat_select(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
) -> None:
    dialog_manager.dialog_data[PROMO_STAT_ID_KEY] = int(dialog_manager.item_id)  # type: ignore[attr-defined]
    await dialog_manager.switch_to(DashboardStatistics.PROMOCODE_DETAIL)


async def on_promo_stat_page_next(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
) -> None:
    page = dialog_manager.dialog_data.get(PROMO_STAT_PAGE_KEY, 0)
    dialog_manager.dialog_data[PROMO_STAT_PAGE_KEY] = page + 1


async def on_promo_stat_page_prev(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
) -> None:
    page = dialog_manager.dialog_data.get(PROMO_STAT_PAGE_KEY, 0)
    dialog_manager.dialog_data[PROMO_STAT_PAGE_KEY] = max(0, page - 1)
