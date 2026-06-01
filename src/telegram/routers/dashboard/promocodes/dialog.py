from aiogram.enums import ButtonStyle
from aiogram_dialog import Dialog, StartMode, Window
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.style import Style
from aiogram_dialog.widgets.text import Format
from magic_filter import F

from src.core.enums import BannerName, PromocodeAvailability
from src.telegram.keyboards import main_menu_button
from src.telegram.states import Dashboard, DashboardPromocodes
from src.telegram.widgets import Banner, I18nFormat, IgnoreUpdate
from src.telegram.widgets.kbd import Button, ListGroup, Row, Start, SwitchTo

from .getters import (
    getter_allowed,
    getter_availability_select,
    getter_configurator,
    getter_lifetime,
    getter_max_activations,
    getter_plan_duration_select,
    getter_plan_select,
    getter_promocodes_main,
    getter_type_select,
)
from .handlers import (
    on_allowed_id_input,
    on_allowed_id_remove,
    on_availability_select,
    on_code_input,
    on_create_promo,
    on_delete_promo,
    on_lifetime_input,
    on_lifetime_reset,
    on_max_activations_input,
    on_max_activations_reset,
    on_page_next,
    on_page_prev,
    on_plan_duration_select,
    on_plan_select,
    on_promo_confirm,
    on_promo_select,
    on_reward_input,
    on_toggle_active,
    on_type_select,
)

promocodes_main = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-promocodes-main"),
    ListGroup(
        Row(
            Button(
                text=I18nFormat(
                    "btn-promocodes.item",
                    code=F["item"]["code"],
                    reward_type=F["item"]["reward_type"],
                ),
                id="promo_item",
                on_click=on_promo_select,
            ),
        ),
        id="promos_list",
        item_id_getter=lambda item: item["id"],
        items="promos",
        when=F["has_promos"],
    ),
    Row(
        Button(
            text=I18nFormat("btn-common.prev"),
            id="page_prev",
            on_click=on_page_prev,
            when=F["has_prev"],
        ),
        Button(
            text=I18nFormat("btn-common.next"),
            id="page_next",
            on_click=on_page_next,
            when=F["has_next"],
        ),
    ),
    Row(
        Button(
            text=I18nFormat("btn-common.create"),
            id="create_promo",
            on_click=on_create_promo,
            when=F["can_manage"],
        ),
    ),
    Row(
        Start(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=Dashboard.MAIN,
            mode=StartMode.RESET_STACK,
        ),
        *main_menu_button,
    ),
    IgnoreUpdate(),
    state=DashboardPromocodes.MAIN,
    getter=getter_promocodes_main,
)

configurator = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat(
        "msg-promocode-configurator",
        code=F["code"],
        is_active=F["is_active"],
        reward=F["reward"],
        plan_name=F["plan_name"],
        promocode_type=F["promocode_type"],
        availability_type=F["availability_type"],
        lifetime=F["lifetime"],
        max_activations=F["max_activations"],
    ),
    Row(
        Button(
            text=I18nFormat("btn-promocode.active-toggle", is_active=F["is_active"]),
            id="toggle_active",
            on_click=on_toggle_active,
            when=F["is_edit"] & F["can_manage"],
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-promocode.code"),
            id="code",
            state=DashboardPromocodes.CODE,
        ),
        SwitchTo(
            text=I18nFormat("btn-promocode.type"),
            id="type",
            state=DashboardPromocodes.TYPE,
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-promocode.reward"),
            id="reward",
            state=DashboardPromocodes.REWARD,
            when=~F["is_subscription"],
        ),
        SwitchTo(
            text=I18nFormat("btn-promocode.plan"),
            id="plan",
            state=DashboardPromocodes.PLAN,
            when=F["is_subscription"],
        ),
        SwitchTo(
            text=I18nFormat("btn-promocode.availability"),
            id="availability",
            state=DashboardPromocodes.AVAILABILITY,
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-promocode.lifetime"),
            id="lifetime",
            state=DashboardPromocodes.LIFETIME,
        ),
        SwitchTo(
            text=I18nFormat("btn-promocode.max-activations"),
            id="max_activations",
            state=DashboardPromocodes.MAX_ACTIVATIONS,
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-promocode.allowed"),
            id="allowed",
            state=DashboardPromocodes.ALLOWED,
            when=F["availability"] == PromocodeAvailability.ALLOWED.value,
        ),
    ),
    Row(
        Button(
            text=I18nFormat("btn-promocode.create"),
            id="confirm_create",
            on_click=on_promo_confirm,
            style=Style(ButtonStyle.SUCCESS),
            when=~F["is_edit"] & F["can_manage"],
        ),
    ),
    Row(
        Button(
            text=I18nFormat("btn-promocode.save"),
            id="confirm_save",
            on_click=on_promo_confirm,
            style=Style(ButtonStyle.SUCCESS),
            when=F["is_edit"] & F["can_manage"],
        ),
        Button(
            text=I18nFormat("btn-promocode.delete"),
            id="delete_promo",
            on_click=on_delete_promo,
            style=Style(ButtonStyle.DANGER),
            when=F["is_edit"] & F["can_manage"],
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back_list",
            state=DashboardPromocodes.MAIN,
        ),
    ),
    IgnoreUpdate(),
    state=DashboardPromocodes.CONFIGURATOR,
    getter=getter_configurator,
)

code_input = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-promocode-input-code"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardPromocodes.CONFIGURATOR,
        ),
    ),
    MessageInput(func=on_code_input),
    IgnoreUpdate(),
    state=DashboardPromocodes.CODE,
)

type_select = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-promocode-select-type"),
    ListGroup(
        Row(
            Button(
                text=Format("{item[label]}"),
                id="type_item",
                on_click=on_type_select,
            ),
        ),
        id="types_list",
        item_id_getter=lambda item: item["value"],
        items="types",
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardPromocodes.CONFIGURATOR,
        ),
    ),
    IgnoreUpdate(),
    state=DashboardPromocodes.TYPE,
    getter=getter_type_select,
)

reward_input = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-promocode-input-reward"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardPromocodes.CONFIGURATOR,
        ),
    ),
    MessageInput(func=on_reward_input),
    IgnoreUpdate(),
    state=DashboardPromocodes.REWARD,
)

plan_select = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-promocode-select-plan"),
    ListGroup(
        Row(
            Button(
                text=Format("{item[name]}"),
                id="plan_item",
                on_click=on_plan_select,
            ),
        ),
        id="plans_list",
        item_id_getter=lambda item: item["id"],
        items="plans",
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardPromocodes.CONFIGURATOR,
        ),
    ),
    IgnoreUpdate(),
    state=DashboardPromocodes.PLAN,
    getter=getter_plan_select,
)

plan_duration_select = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-promocode-select-plan-duration"),
    ListGroup(
        Row(
            Button(
                text=I18nFormat("btn-promocode.plan-duration", days=F["item"]["days"]),
                id="plan_duration_item",
                on_click=on_plan_duration_select,
            ),
        ),
        id="plan_durations_list",
        item_id_getter=lambda item: item["days"],
        items="durations",
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardPromocodes.PLAN,
        ),
    ),
    IgnoreUpdate(),
    state=DashboardPromocodes.PLAN_DURATION,
    getter=getter_plan_duration_select,
)

availability_select = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-promocode-select-availability"),
    ListGroup(
        Row(
            Button(
                text=Format("{item[label]}"),
                id="avail_item",
                on_click=on_availability_select,
            ),
        ),
        id="availability_list",
        item_id_getter=lambda item: item["value"],
        items="availability_types",
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardPromocodes.CONFIGURATOR,
        ),
    ),
    IgnoreUpdate(),
    state=DashboardPromocodes.AVAILABILITY,
    getter=getter_availability_select,
)

allowed_ids = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-promocode-allowed-ids"),
    ListGroup(
        Row(
            Button(
                text=Format("{item} ❌"),
                id="remove_id",
                on_click=on_allowed_id_remove,
            ),
        ),
        id="allowed_ids_list",
        item_id_getter=lambda item: item,
        items="allowed_ids",
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardPromocodes.CONFIGURATOR,
        ),
    ),
    MessageInput(func=on_allowed_id_input),
    IgnoreUpdate(),
    state=DashboardPromocodes.ALLOWED,
    getter=getter_allowed,
)

lifetime_input = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-promocode-input-lifetime"),
    Row(
        Button(
            text=I18nFormat("btn-promocode.reset"),
            id="reset",
            on_click=on_lifetime_reset,
            when=F["has_lifetime"],
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardPromocodes.CONFIGURATOR,
        ),
    ),
    MessageInput(func=on_lifetime_input),
    IgnoreUpdate(),
    state=DashboardPromocodes.LIFETIME,
    getter=getter_lifetime,
)

max_activations_input = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-promocode-input-max-activations"),
    Row(
        Button(
            text=I18nFormat("btn-promocode.reset"),
            id="reset",
            on_click=on_max_activations_reset,
            when=F["has_max_activations"],
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardPromocodes.CONFIGURATOR,
        ),
    ),
    MessageInput(func=on_max_activations_input),
    IgnoreUpdate(),
    state=DashboardPromocodes.MAX_ACTIVATIONS,
    getter=getter_max_activations,
)

router = Dialog(
    promocodes_main,
    configurator,
    code_input,
    type_select,
    reward_input,
    plan_select,
    plan_duration_select,
    availability_select,
    allowed_ids,
    lifetime_input,
    max_activations_input,
)
