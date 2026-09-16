# ruff: noqa: PLC0415
import pytest

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]


async def test_startup_lifespan_is_clean(smoke_app_session):
    errors = smoke_app_session.startup_errors
    assert not errors, "\n\n".join(f"[{e.source}]\n{e.text}" for e in errors)
    names = smoke_app_session.startup_call_names
    assert "SetWebhook" in names
    assert "SetMyCommands" in names


async def test_start_renders_main_menu(app):
    from tests.smoke.harness.client import TgUser

    user = TgUser(app, app.seed.newbie_tg, "Newbie", "newbie")
    await user.send("/start")
    assert user.screen is not None, app.session.call_names()
    assert user.widget_ids(), user.buttons()
