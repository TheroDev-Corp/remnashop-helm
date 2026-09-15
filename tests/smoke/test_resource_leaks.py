# ruff: noqa: PLC0415
"""Connection-pool hygiene: handled updates must give their DB connection back."""

from __future__ import annotations

import gc

import pytest

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]

# Regression guard: every project middleware on the `errors` observer must be registered after
# dishka's ContainerMiddleware (setup_error_middleware). Before, UserMiddleware resolved deps from
# the already-closed update container and leaked a pooled connection on every handled error.


async def _checked_out(app) -> int:
    from sqlalchemy.ext.asyncio import AsyncEngine

    await app.settle()
    gc.collect()
    engine = await app.container.get(AsyncEngine)
    return engine.pool.checkedout()


async def _subscriber(app):
    from tests.smoke.harness.client import TgUser

    user = TgUser(app, app.seed.subscriber_tg, "Subscriber", "subscriber")
    await user.send("/start")
    return user


def _errors_chain(smoke_app) -> list[object]:
    return list(getattr(smoke_app.dispatcher.errors.outer_middleware, "_middlewares", []))


async def test_normal_clicks_release_connections(app):
    user = await _subscriber(app)
    baseline = await _checked_out(app)
    for _ in range(5):
        await user.click("invite")
        await user.click("back")
    assert await _checked_out(app) == baseline


async def test_stale_callbacks_release_connections(app):
    from aiogram_dialog.api.exceptions import UnknownIntent
    from aiogram_dialog.utils import CB_SEP

    user = await _subscriber(app)
    baseline = await _checked_out(app)
    with app.expect_errors(UnknownIntent):
        for i in range(5):
            await user.click_data(f"deadbeef{i:08d}{CB_SEP}devices")
    leaked = await _checked_out(app) - baseline
    assert leaked == 0, f"{leaked} pooled connections still checked out after 5 stale callbacks"


async def test_handler_exception_releases_connections(app):
    """Any exception that reaches the errors observer (not only lost dialog context) leaks."""
    from tests.smoke.harness.client import TgUser

    newbie = TgUser(app, app.seed.newbie_tg, "Newbie", "newbie")
    await newbie.send("/start")
    stale_trial = newbie.find("trial_free").callback_data or ""
    await newbie.click("trial_free")  # consumes the trial
    await newbie.send("/start")
    baseline = await _checked_out(app)
    with app.expect_errors(Exception):
        await newbie.click_data(stale_trial)  # old menu message, trial no longer available
    assert await _checked_out(app) == baseline


async def test_error_middleware_runs_inside_its_own_request_scope(smoke_app_session):
    from dishka.integrations.aiogram import ContainerMiddleware

    from src.telegram.middlewares import ErrorMiddleware

    chain = _errors_chain(smoke_app_session)
    names = [type(m).__name__ for m in chain]
    error_index = next(i for i, m in enumerate(chain) if isinstance(m, ErrorMiddleware))
    container_index = next(i for i, m in enumerate(chain) if isinstance(m, ContainerMiddleware))
    assert container_index < error_index, names


async def test_every_errors_middleware_runs_inside_request_scope(smoke_app_session):
    from dishka.integrations.aiogram import ContainerMiddleware

    from src.telegram.middlewares import EventTypedMiddleware

    chain = _errors_chain(smoke_app_session)
    names = [type(m).__name__ for m in chain]
    container_index = next(i for i, m in enumerate(chain) if isinstance(m, ContainerMiddleware))
    early = [
        type(m).__name__ for m in chain[:container_index] if isinstance(m, EventTypedMiddleware)
    ]
    assert not early, f"run before dishka's request scope on errors: {early}; chain={names}"
