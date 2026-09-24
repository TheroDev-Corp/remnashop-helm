# ruff: noqa: PLC0415
"""Expiry reminders: admin settings screen and the hourly fallback scan on a real DB/Redis."""

from __future__ import annotations

import pytest

from tests.smoke.test_admin_flows import _owner_dashboard, _run_steps

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]


async def _expiry_settings(app):
    from dishka import Scope

    from src.application.common.dao import SettingsDao

    async with app.container(scope=Scope.REQUEST) as request:
        settings = await (await request.get(SettingsDao)).get()
    return settings.notifications.expiry_reminder


async def test_admin_edits_expiry_reminder_settings(app):
    owner = await _owner_dashboard(app)
    before = await _expiry_settings(app)

    await _run_steps(
        app,
        owner,
        [
            "remnashop",
            "notifications",
            "users",
            "expiry_reminder",
            "expect:RemnashopNotifications:EXPIRY_REMINDER",
            "fallback_toggle",
            "days",
            "send:abc",
            "expect:RemnashopNotifications:EXPIRY_REMINDER_DAYS",
            "send:1, 7 3",
            "expect:RemnashopNotifications:EXPIRY_REMINDER",
            "fallback_toggle",
        ],
    )

    after = await _expiry_settings(app)
    assert after.days == [7, 3, 1]
    assert after.fallback_enabled == before.fallback_enabled
    assert "7, 3, 1" in owner.screen_text()


async def test_fallback_scan_reminds_once(app):
    from dishka import Scope
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncEngine

    from src.application.services import ExpiryReminderService

    engine = await app.container.get(AsyncEngine)

    async def set_expire(interval: str) -> None:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f"UPDATE subscriptions SET expire_at = now() + interval '{interval}' "
                    "WHERE id = :id"
                ),
                {"id": app.seed.subscription_id},
            )

    async def scan() -> int:
        async with app.container(scope=Scope.REQUEST) as request:
            return await (await request.get(ExpiryReminderService)).check_expiring()

    await set_expire("2 days 20 hours")
    try:
        before = len(app.session.calls)
        assert await scan() == 1
        assert await scan() == 0  # already sent for this expire_at
        await app.settle()
        sent = [
            c
            for c in app.session.calls[before:]
            if type(c).__name__ == "SendMessage" and c.chat_id == app.seed.subscriber_tg
        ]
        assert len(sent) == 1, sent
    finally:
        await set_expire("30 days")
