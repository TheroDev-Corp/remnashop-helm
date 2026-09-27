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
