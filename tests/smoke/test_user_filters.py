# ruff: noqa: PLC0415
"""Admin user filters: counts, a segment list, and the round trip through the user card."""

from __future__ import annotations

import pytest

from tests.smoke.test_admin_flows import _owner_dashboard

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]


async def _set_subscriber_expire(app, interval: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncEngine

    engine = await app.container.get(AsyncEngine)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                f"UPDATE subscriptions SET expire_at = now() + interval '{interval}' WHERE id = :id"
            ),
            {"id": app.seed.subscription_id},
        )


async def _filter_user_ids(app, user_filter) -> set[int]:
    from dishka import Scope

    from src.application.common.dao import UserDao

    async with app.container(scope=Scope.REQUEST) as request:
        dao = await request.get(UserDao)
        users = await dao.get_by_filter(user_filter)
        counts = await dao.count_by_filters()
    assert counts[user_filter] == len(users)
    return {u.id for u in users}


async def test_filters_segment_users(app):
    from src.core.enums import UserFilter

    seed = app.seed
    assert seed.subscriber_id in await _filter_user_ids(app, UserFilter.ACTIVE)
    assert seed.newbie_id in await _filter_user_ids(app, UserFilter.NO_SUBSCRIPTION)

    await _set_subscriber_expire(app, "3 days")
    try:
        assert seed.subscriber_id in await _filter_user_ids(app, UserFilter.EXPIRING)

        # Status still ACTIVE in the DB, but the date has passed: the panel webhook was lost.
        await _set_subscriber_expire(app, "-1 day")
        assert seed.subscriber_id in await _filter_user_ids(app, UserFilter.EXPIRED)
        assert seed.subscriber_id not in await _filter_user_ids(app, UserFilter.ACTIVE)

        owner = await _owner_dashboard(app)
        await owner.click("users")
        await owner.click("filters")
        assert app.current_state(owner.id) == "DashboardUsers:FILTERS"
        await owner.click("filter:EXPIRED")
        assert app.current_state(owner.id) == "DashboardUsers:FILTER_RESULTS"

        await owner.click(f"user:{seed.subscriber_id}")
        assert app.current_state(owner.id) == "DashboardUser:MAIN"
        await owner.click("back")
        assert app.current_state(owner.id) == "DashboardUsers:FILTER_RESULTS"
        assert owner.has(f"user:{seed.subscriber_id}")
    finally:
        await _set_subscriber_expire(app, "30 days")


async def _filter_ids_scoped(app, user_filter, **scope) -> set[int]:
    from dishka import Scope

    from src.application.common.dao import UserDao

    async with app.container(scope=Scope.REQUEST) as request:
        dao = await request.get(UserDao)
        users = await dao.get_by_filter(user_filter, **scope)
        counts = await dao.count_by_filters(**scope)
    assert counts[user_filter] == len(users)
    return {u.id for u in users}


async def test_panel_imports_not_in_bot_are_filterable_and_manageable(app):
    from dishka import Scope

    from src.application.common.dao import UserDao
    from src.core.enums import UserFilter, UserSource
    from tests.smoke.harness.client import TgUser
    from tests.smoke.test_admin_flows import _run_steps

    imported_tg = 555_000_777
    app.panel.add_user(username="panel_with_tg", telegram_id=imported_tg)
    without_tg = app.panel.add_user(username="panel_no_tg", email="no-tg@example.com")

    owner = await _owner_dashboard(app)
    await _run_steps(app, owner, ["importer", "sync_panel", "sync_panel_start"])
    await app.settle()

    async with app.container(scope=Scope.REQUEST) as request:
        dao = await request.get(UserDao)
        user_with_tg = await dao.get_by_telegram_id(imported_tg)
        user_without_tg = await dao.get_by_remna_id(without_tg["id"])
    assert user_with_tg and user_without_tg
    new_ids = {user_with_tg.id, user_without_tg.id}

    scope = {"source": UserSource.PANEL, "not_in_bot": True}
    assert new_ids <= await _filter_ids_scoped(app, UserFilter.ALL, **scope)
    assert new_ids <= await _filter_ids_scoped(app, UserFilter.ACTIVE, **scope)
    assert app.seed.subscriber_id not in await _filter_ids_scoped(
        app, UserFilter.ALL, source=UserSource.PANEL
    )
    self_registered = await _filter_ids_scoped(app, UserFilter.ALL, source=UserSource.SELF)
    assert app.seed.subscriber_id in self_registered
    assert not new_ids & self_registered
    assert app.seed.owner_id not in await _filter_ids_scoped(app, UserFilter.ALL, not_in_bot=True)

    # Admin reaches a telegram-less panel user through the filters and extends it on the panel.
    expire_before = app.panel.users[without_tg["id"]]["expireAt"]
    owner = await _owner_dashboard(app)
    await owner.click("users")
    await owner.click("filters")
    await owner.click("source")  # ANY -> PANEL
    await owner.click("not_in_bot")
    await owner.click("filter:ALL")
    assert "из панели" in owner.screen_text() and "похоже, не в боте" in owner.screen_text()
    await owner.click(f"user:{user_without_tg.id}")
    await _run_steps(app, owner, ["subscription", "expire_time", "send:10"])
    assert app.panel.users[without_tg["id"]]["expireAt"] != expire_before

    # Back from the card keeps both scope toggles.
    await _run_steps(app, owner, ["back", "back", "back", "expect:DashboardUsers:FILTER_RESULTS"])
    assert owner.has(f"user:{user_with_tg.id}")

    # First message to the bot overwrites the import defaults (username, name): still imported,
    # no longer "not in bot".
    await TgUser(app, imported_tg, "Imported", "imported").send("/start")
    assert user_with_tg.id in await _filter_ids_scoped(app, UserFilter.ALL, source=UserSource.PANEL)
    assert user_with_tg.id not in await _filter_ids_scoped(app, UserFilter.ALL, **scope)


async def test_not_in_bot_ignores_self_registered_users_without_a_username(app):
    """The heuristic keys on the panel import, not on a missing username alone.

    A Telegram account without a username whose display name happens to be its own id looks
    exactly like a fresh panel import on the `users` row; only the IMPORTED subscription tells
    them apart.
    """
    from dishka import Scope

    from src.application.common.dao import UserDao
    from src.core.enums import UserFilter
    from tests.smoke.harness.client import TgUser

    lookalike_tg = 555_000_999
    await TgUser(app, lookalike_tg, str(lookalike_tg)).send("/start")

    async with app.container(scope=Scope.REQUEST) as request:
        dao = await request.get(UserDao)
        lookalike = await dao.get_by_telegram_id(lookalike_tg)
    assert lookalike and lookalike.username is None and lookalike.name == str(lookalike_tg)

    assert lookalike.id not in await _filter_ids_scoped(app, UserFilter.ALL, not_in_bot=True)
