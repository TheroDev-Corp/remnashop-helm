from typing import Any

from adaptix import Retort
from aiogram_dialog import DialogManager
from dishka import FromDishka
from dishka.integrations.aiogram_dialog import inject

from src.application.common.dao import SettingsDao, UserDao
from src.application.dto import TelegramUserDto, UserDto
from src.application.use_cases.user.queries.activity import GetRecentActivityUsers
from src.core.constants import RECENT_REGISTERED_MAX_COUNT
from src.core.utils.converters import percent


@inject
async def search_results_getter(
    dialog_manager: DialogManager,
    retort: FromDishka[Retort],
    **kwargs: Any,
) -> dict[str, Any]:
    found_users_data: list[str] = dialog_manager.start_data["found_users"]  # type: ignore[call-overload, index, assignment]
    found_users: list[UserDto] = retort.load(found_users_data, list[UserDto])

    return {
        "found_users": found_users,
        "count": len(found_users),
    }


@inject
async def recent_registered_getter(
    dialog_manager: DialogManager,
    user_dao: FromDishka[UserDao],
    **kwargs: Any,
) -> dict[str, Any]:
    users = await user_dao.get_recent_registered_users(limit=RECENT_REGISTERED_MAX_COUNT)
    return {"recent_registered_users": users}


@inject
async def recent_activity_getter(
    dialog_manager: DialogManager,
    user: TelegramUserDto,
    get_recent_activity_users: FromDishka[GetRecentActivityUsers],
    **kwargs: Any,
) -> dict[str, Any]:
    users = await get_recent_activity_users.system([user.id])
    return {"recent_activity_users": users}


@inject
async def blacklist_getter(
    dialog_manager: DialogManager,
    user_dao: FromDishka[UserDao],
    **kwargs: Any,
) -> dict[str, Any]:
    blocked_users = await user_dao.get_blocked_users()
    count_users = await user_dao.count()

    return {
        "blocked_users_exists": bool(blocked_users),
        "blocked_users": blocked_users,
        "count_blocked": len(blocked_users),
        "count_users": count_users,
        "percent": percent(part=len(blocked_users), whole=count_users),
    }


@inject
async def blacklist_users_getter(
    dialog_manager: DialogManager,
    user_dao: FromDishka[UserDao],
    **kwargs: Any,
) -> dict[str, Any]:
    blocked_users = await user_dao.get_blocked_users()
    count_users = await user_dao.count()
    return {
        "blocked_users": blocked_users,
        "blocked_users_exists": bool(blocked_users),
        "count_blocked": len(blocked_users),
        "count_users": count_users,
        "percent": percent(part=len(blocked_users), whole=count_users),
    }


@inject
async def blacklist_sources_getter(
    dialog_manager: DialogManager,
    settings_dao: FromDishka[SettingsDao],
    **kwargs: Any,
) -> dict[str, Any]:
    settings = await settings_dao.get()
    sources = settings.blacklist.sources

    items = []
    for s in sources:
        label = s.name or (s.url[:40] + "…" if len(s.url) > 40 else s.url)
        items.append({"id": s.id, "source": label})

    return {
        "sources": items,
        "sources_count": len(items),
        "has_sources": bool(items),
    }
