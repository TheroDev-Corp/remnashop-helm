from aiogram_dialog import Dialog, StartMode, Window
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.text import Format
from magic_filter import F

from src.core.enums import BannerName
from src.telegram.keyboards import main_menu_button
from src.telegram.states import Dashboard, DashboardUsers
from src.telegram.widgets import Banner, I18nFormat, IgnoreUpdate
from src.telegram.widgets.kbd import Button, ListGroup, Row, ScrollingGroup, Select, Start, SwitchTo

from .getters import (
    blacklist_getter,
    blacklist_sources_getter,
    blacklist_users_getter,
    recent_activity_getter,
    recent_registered_getter,
    search_results_getter,
)
from .handlers import (
    on_blacklist_view,
    on_block_input,
    on_clear_blocked_ids,
    on_source_add_input,
    on_source_delete,
    on_source_sync,
    on_unblock_all,
    on_user_search,
    on_user_select,
)

users = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-users-main"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-users.search"),
            id="search",
            state=DashboardUsers.SEARCH,
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-users.recent-registered"),
            id="recent_registered",
            state=DashboardUsers.RECENT_REGISTERED,
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-users.recent-activity"),
            id="recent_activity",
            state=DashboardUsers.RECENT_ACTIVITY,
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-users.blacklist"),
            id="blacklist",
            state=DashboardUsers.BLACKLIST,
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
    state=DashboardUsers.MAIN,
)

search = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-users-search"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardUsers.MAIN,
        ),
    ),
    MessageInput(func=on_user_search),
    IgnoreUpdate(),
    state=DashboardUsers.SEARCH,
)

recent_registered = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-users-recent-registered"),
    ScrollingGroup(
        Select(
            text=Format("{item.name}"),
            id="user",
            item_id_getter=lambda item: item.id,
            items="recent_registered_users",
            type_factory=int,
            on_click=on_user_select,
        ),
        id="scroll",
        width=1,
        height=7,
        hide_on_single_page=True,
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardUsers.MAIN,
        ),
    ),
    IgnoreUpdate(),
    state=DashboardUsers.RECENT_REGISTERED,
    getter=recent_registered_getter,
)

recent_activity = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-users-recent-activity"),
    ScrollingGroup(
        Select(
            text=Format("{item.name}"),
            id="user",
            item_id_getter=lambda item: item.id,
            items="recent_activity_users",
            type_factory=int,
            on_click=on_user_select,
        ),
        id="scroll",
        width=1,
        height=7,
        hide_on_single_page=True,
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardUsers.MAIN,
        ),
    ),
    IgnoreUpdate(),
    state=DashboardUsers.RECENT_ACTIVITY,
    getter=recent_activity_getter,
)

search_results = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-users-search-results", count=F["count"]),
    ScrollingGroup(
        Select(
            text=Format("{item.name}"),
            id="user",
            item_id_getter=lambda item: item.id,
            items="found_users",
            type_factory=int,
            on_click=on_user_select,
        ),
        id="scroll",
        width=1,
        height=7,
        hide_on_single_page=True,
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardUsers.SEARCH,
        ),
    ),
    IgnoreUpdate(),
    state=DashboardUsers.SEARCH_RESULTS,
    getter=search_results_getter,
)


blacklist = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-users-blacklist"),
    Row(
        Button(
            text=I18nFormat("btn-users.blacklist-view"),
            id="blacklist_view",
            on_click=on_blacklist_view,
        ),
    ),
    Row(
        Button(
            text=I18nFormat("btn-users.unblock-all"),
            id="unblock_all",
            on_click=on_unblock_all,
            when=F["blocked_users_exists"],
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-users.blacklist-block"),
            id="block",
            state=DashboardUsers.BLACKLIST_BLOCK,
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardUsers.MAIN,
        ),
    ),
    IgnoreUpdate(),
    state=DashboardUsers.BLACKLIST,
    getter=blacklist_getter,
)

blacklist_users = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-users-blacklist-list"),
    ScrollingGroup(
        Select(
            text=Format("{item.name}"),
            id="user",
            item_id_getter=lambda item: item.id,
            items="blocked_users",
            type_factory=int,
            on_click=on_user_select,
        ),
        id="scroll",
        width=1,
        height=7,
        hide_on_single_page=True,
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardUsers.BLACKLIST,
        ),
    ),
    IgnoreUpdate(),
    state=DashboardUsers.BLACKLIST_USERS,
    getter=blacklist_users_getter,
)

blacklist_block = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-users-blacklist-block"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-users.blacklist-sources"),
            id="sources",
            state=DashboardUsers.BLACKLIST_SOURCES,
        ),
    ),
    Row(
        Button(
            text=I18nFormat("btn-users.blacklist-block-clear"),
            id="clear_blocked_ids",
            on_click=on_clear_blocked_ids,
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardUsers.BLACKLIST,
        ),
    ),
    MessageInput(func=on_block_input),
    IgnoreUpdate(),
    state=DashboardUsers.BLACKLIST_BLOCK,
)

blacklist_sources = Window(
    Banner(BannerName.DASHBOARD),
    I18nFormat("msg-users-blacklist-sources"),
    ScrollingGroup(
        ListGroup(
            Button(
                text=I18nFormat("btn-users.blacklist-source", source=F["item"]["source"]),
                id="delete",
                on_click=on_source_delete,
            ),
            id="sources_list",
            item_id_getter=lambda item: item["id"],
            items="sources",
        ),
        id="scroll",
        width=1,
        height=6,
        hide_on_single_page=True,
        when=F["has_sources"],
    ),
    Row(
        Button(
            text=I18nFormat("btn-users.blacklist-sources-sync"),
            id="sync",
            on_click=on_source_sync,
            when=F["has_sources"],
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=DashboardUsers.BLACKLIST_BLOCK,
        ),
    ),
    MessageInput(func=on_source_add_input),
    IgnoreUpdate(),
    state=DashboardUsers.BLACKLIST_SOURCES,
    getter=blacklist_sources_getter,
)

router = Dialog(
    users,
    search,
    recent_registered,
    recent_activity,
    search_results,
    blacklist,
    blacklist_users,
    blacklist_block,
    blacklist_sources,
)
