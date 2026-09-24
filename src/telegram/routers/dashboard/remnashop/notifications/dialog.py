from aiogram_dialog import Dialog, Window
from aiogram_dialog.widgets.input import MessageInput
from magic_filter import F

from src.core.enums import BannerName, SystemNotificationType, UserNotificationType
from src.telegram.keyboards import main_menu_button
from src.telegram.states import DashboardRemnashop, RemnashopNotifications
from src.telegram.widgets import Banner, I18nFormat, IgnoreUpdate
from src.telegram.widgets.kbd import Button, Column, Row, Select, Start, SwitchTo

from .getters import (
    expiry_reminder_getter,
    system_default_route_getter,
    system_route_getter,
    system_type_getter,
    system_types_getter,
    user_types_getter,
)
from .handlers import (
    on_default_route_chat_id_input,
    on_default_route_clear,
    on_default_route_thread_id_input,
    on_expiry_days_input,
    on_expiry_fallback_toggle,
    on_route_chat_id_input,
    on_route_clear,
    on_route_thread_id_input,
    on_system_type_select,
    on_system_type_toggle,
    on_user_type_select,
)

notifications = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-notifications-main"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-notifications.user"),
            id="users",
            state=RemnashopNotifications.USER,
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-notifications.system"),
            id="system",
            state=RemnashopNotifications.SYSTEM,
        ),
    ),
    Row(
        Start(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardRemnashop.MAIN,
        ),
        *main_menu_button,
    ),
    IgnoreUpdate(),
    state=RemnashopNotifications.MAIN,
)

user = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-notifications-user"),
    Column(
        Select(
            text=I18nFormat(
                "btn-notifications.user-choice",
                notification_type=F["item"]["notification_type"],
                enabled=F["item"]["enabled"],
            ),
            id="type_select",
            item_id_getter=lambda item: item["notification_type"],
            items="types",
            type_factory=UserNotificationType,
            on_click=on_user_type_select,
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-notifications.expiry-reminder"),
            id="expiry_reminder",
            state=RemnashopNotifications.EXPIRY_REMINDER,
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=RemnashopNotifications.MAIN,
        ),
    ),
    IgnoreUpdate(),
    state=RemnashopNotifications.USER,
    getter=user_types_getter,
)

expiry_reminder = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-notifications-expiry-reminder"),
    Row(
        Button(
            text=I18nFormat("btn-notifications.expiry-fallback-toggle"),
            id="fallback_toggle",
            on_click=on_expiry_fallback_toggle,
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-notifications.expiry-days"),
            id="days",
            state=RemnashopNotifications.EXPIRY_REMINDER_DAYS,
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=RemnashopNotifications.USER,
        ),
    ),
    IgnoreUpdate(),
    state=RemnashopNotifications.EXPIRY_REMINDER,
    getter=expiry_reminder_getter,
)

expiry_reminder_days = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-notifications-expiry-reminder-days"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=RemnashopNotifications.EXPIRY_REMINDER,
        ),
    ),
    MessageInput(func=on_expiry_days_input),
    IgnoreUpdate(),
    state=RemnashopNotifications.EXPIRY_REMINDER_DAYS,
    getter=expiry_reminder_getter,
)

system = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-notifications-system"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-notifications.default-route"),
            id="default_route",
            state=RemnashopNotifications.SYSTEM_DEFAULT_ROUTE,
        ),
    ),
    Column(
        Select(
            text=I18nFormat(
                "btn-notifications.system-choice",
                notification_type=F["item"]["notification_type"],
                enabled=F["item"]["enabled"],
                has_route=F["item"]["has_route"],
            ),
            id="type_select",
            item_id_getter=lambda item: item["notification_type"],
            items="types",
            type_factory=SystemNotificationType,
            on_click=on_system_type_select,
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=RemnashopNotifications.MAIN,
        ),
    ),
    IgnoreUpdate(),
    state=RemnashopNotifications.SYSTEM,
    getter=system_types_getter,
)

system_type = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-notifications-system-type"),
    Row(
        Button(
            text=I18nFormat("btn-notifications.active-toggle"),
            id="toggle",
            on_click=on_system_type_toggle,
            when=F["can_toggle"],
        ),
        SwitchTo(
            text=I18nFormat("btn-notifications.route"),
            id="route",
            state=RemnashopNotifications.SYSTEM_ROUTE,
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=RemnashopNotifications.SYSTEM,
        ),
    ),
    IgnoreUpdate(),
    state=RemnashopNotifications.SYSTEM_TYPE,
    getter=system_type_getter,
)

system_route = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-notifications-system-route"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-notifications.chat-id"),
            id="edit_chat",
            state=RemnashopNotifications.SYSTEM_ROUTE_CHAT_ID,
        ),
        SwitchTo(
            text=I18nFormat("btn-notifications.thread-id"),
            id="edit_thread",
            state=RemnashopNotifications.SYSTEM_ROUTE_THREAD_ID,
        ),
    ),
    Row(
        Button(
            text=I18nFormat("btn-notifications.route-clear"),
            id="clear_route",
            on_click=on_route_clear,
            when=F["has_route"],
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=RemnashopNotifications.SYSTEM_TYPE,
        ),
    ),
    IgnoreUpdate(),
    state=RemnashopNotifications.SYSTEM_ROUTE,
    getter=system_route_getter,
)

system_route_chat_id = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-notifications-system-route-chat-id"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=RemnashopNotifications.SYSTEM_ROUTE,
        ),
    ),
    MessageInput(func=on_route_chat_id_input),
    IgnoreUpdate(),
    state=RemnashopNotifications.SYSTEM_ROUTE_CHAT_ID,
    getter=system_route_getter,
)

system_route_thread_id = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-notifications-system-route-thread-id"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=RemnashopNotifications.SYSTEM_ROUTE,
        ),
    ),
    MessageInput(func=on_route_thread_id_input),
    IgnoreUpdate(),
    state=RemnashopNotifications.SYSTEM_ROUTE_THREAD_ID,
    getter=system_route_getter,
)

system_default_route = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-notifications-system-default-route"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-notifications.chat-id"),
            id="edit_chat",
            state=RemnashopNotifications.SYSTEM_DEFAULT_ROUTE_CHAT_ID,
        ),
        SwitchTo(
            text=I18nFormat("btn-notifications.thread-id"),
            id="edit_thread",
            state=RemnashopNotifications.SYSTEM_DEFAULT_ROUTE_THREAD_ID,
        ),
    ),
    Row(
        Button(
            text=I18nFormat("btn-notifications.route-clear"),
            id="clear_route",
            on_click=on_default_route_clear,
            when=F["has_route"],
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=RemnashopNotifications.SYSTEM,
        ),
    ),
    IgnoreUpdate(),
    state=RemnashopNotifications.SYSTEM_DEFAULT_ROUTE,
    getter=system_default_route_getter,
)

system_default_route_chat_id = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-notifications-system-route-chat-id"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=RemnashopNotifications.SYSTEM_DEFAULT_ROUTE,
        ),
    ),
    MessageInput(func=on_default_route_chat_id_input),
    IgnoreUpdate(),
    state=RemnashopNotifications.SYSTEM_DEFAULT_ROUTE_CHAT_ID,
    getter=system_default_route_getter,
)

system_default_route_thread_id = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-notifications-system-route-thread-id"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=RemnashopNotifications.SYSTEM_DEFAULT_ROUTE,
        ),
    ),
    MessageInput(func=on_default_route_thread_id_input),
    IgnoreUpdate(),
    state=RemnashopNotifications.SYSTEM_DEFAULT_ROUTE_THREAD_ID,
    getter=system_default_route_getter,
)

router = Dialog(
    notifications,
    user,
    expiry_reminder,
    expiry_reminder_days,
    system,
    system_type,
    system_route,
    system_route_chat_id,
    system_route_thread_id,
    system_default_route,
    system_default_route_chat_id,
    system_default_route_thread_id,
)
