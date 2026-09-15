"""Regression tests for production error-log fixes (context loss, transient Telegram errors,
Remnawave enable/disable 400s, sync id overwrite, raw plan names, error report files, error
middleware ordering)."""

import re
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
from adaptix import Retort
from aiogram import Dispatcher
from aiogram.exceptions import TelegramEntityTooLarge, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery
from aiogram_dialog import setup_dialogs
from aiogram_dialog.api.exceptions import UnknownIntent, UnknownState
from dishka import Provider, make_async_container
from dishka.integrations.aiogram import ContainerMiddleware, setup_dishka
from fluentogram.exceptions import KeyNotFoundError
from loguru import logger
from remnapy.enums import TrafficLimitStrategy

from src.application.common import BotService, EventPublisher, Notifier, Redirect
from src.application.common.dao import UserDao
from src.application.dto import RemnaSubscriptionDto
from src.application.events import ErrorEvent
from src.application.use_cases.misc.commands.navigation import RedirectMenu
from src.core.constants import CONFIG_KEY, CONTAINER_KEY
from src.core.enums import SubscriptionStatus
from src.core.exceptions import RemnawaveActionError
from src.infrastructure.services.notification import build_error_report
from src.infrastructure.services.remnawave import RemnawaveImpl
from src.infrastructure.services.translator import TranslatorRunnerImpl, _looks_like_i18n_key
from src.telegram.middlewares import setup_error_middleware, setup_middlewares
from src.telegram.middlewares.error import ErrorMiddleware, is_transient_telegram_error

PANEL = "https://panel.example"


# --------------------------------------------------------------------------- helpers


class _Panel:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.calls: list[str] = []

    async def post(self, path: str, json: Any = None) -> httpx.Response:
        self.calls.append(path)
        return self.response


def _service(status: int, body: Any = None) -> tuple[RemnawaveImpl, _Panel]:
    request = httpx.Request("POST", f"{PANEL}/users/7/actions/x")
    response = httpx.Response(status, json=body, request=request)
    panel = _Panel(response)
    sdk = MagicMock()
    sdk._client = panel
    return RemnawaveImpl(sdk=sdk), panel


def _capture_logs() -> tuple[list[tuple[str, str]], int]:
    records: list[tuple[str, str]] = []
    sink_id = logger.add(
        lambda m: records.append((m.record["level"].name, m.record["message"])), level="DEBUG"
    )
    return records, sink_id


class _Container:
    def __init__(self, deps: dict[type, Any]) -> None:
        self.deps = deps
        self.requested: list[type] = []

    async def get(self, key: type) -> Any:
        self.requested.append(key)
        return self.deps[key]


def _error_event(exception: Exception, callback: bool = True) -> MagicMock:
    event = MagicMock()
    event.exception = exception
    event.update.update_id = 1
    event.update.message = None
    if callback:
        event.update.callback_query = MagicMock()
        event.update.callback_query.answer = AsyncMock()
    else:
        event.update.callback_query = None
    return event


def _deps() -> dict[type, Any]:
    user = MagicMock()
    user_dao = MagicMock()
    user_dao.get_by_telegram_id = AsyncMock(return_value=user)
    return {
        BotService: MagicMock(get_support_url=MagicMock(return_value="https://t.me/support")),
        EventPublisher: MagicMock(publish=AsyncMock()),
        Notifier: MagicMock(notify_user=AsyncMock()),
        RedirectMenu: MagicMock(system=AsyncMock()),
        Redirect: MagicMock(to_main_menu=AsyncMock()),
        UserDao: user_dao,
    }


def _data(container: _Container) -> dict[str, Any]:
    aiogram_user = MagicMock()
    aiogram_user.id = 42
    config = MagicMock()
    config.build.data = {}
    return {"event_from_user": aiogram_user, CONFIG_KEY: config, CONTAINER_KEY: container}


def _patch_user(monkeypatch) -> None:
    monkeypatch.setattr(
        ErrorMiddleware, "_get_aiogram_user", lambda self, data: data["event_from_user"]
    )


# --------------------------------------------------------------------------- 1. context loss


@pytest.mark.parametrize(
    "exc", [UnknownIntent("Context not found for intent id: x"), UnknownState("s")]
)
async def test_context_loss_is_handled_and_restarts_main_menu(monkeypatch, exc):
    _patch_user(monkeypatch)
    deps = _deps()
    container = _Container(deps)
    event = _error_event(exc)
    handler = AsyncMock(return_value=None)

    result = await ErrorMiddleware().middleware_logic(handler, event, _data(container))

    assert result is True  # handled -> aiogram's ErrorsMiddleware does not re-raise
    event.update.callback_query.answer.assert_awaited_once()
    deps[Redirect].to_main_menu.assert_awaited_once_with(42)
    deps[Notifier].notify_user.assert_awaited_once()
    assert (
        deps[Notifier].notify_user.await_args.kwargs["i18n_key"] == "ntf-error.lost-context-restart"
    )
    deps[EventPublisher].publish.assert_not_awaited()


# --------------------------------------------------------------------------- 2. transient errors


async def test_network_error_is_warning_without_error_event(monkeypatch):
    _patch_user(monkeypatch)
    deps = _deps()
    container = _Container(deps)
    exc = TelegramNetworkError(
        method=AnswerCallbackQuery(callback_query_id="1"), message="Request timeout error"
    )
    event = _error_event(exc)
    records, sink_id = _capture_logs()
    try:
        result = await ErrorMiddleware().middleware_logic(AsyncMock(), event, _data(container))
    finally:
        logger.remove(sink_id)

    assert result is True
    event.update.callback_query.answer.assert_awaited_once()
    assert container.requested == []  # no ErrorEvent, no notifications
    assert any(level == "WARNING" for level, _ in records)
    assert not any(level == "ERROR" for level, _ in records)


def test_transient_classification():
    method = AnswerCallbackQuery(callback_query_id="1")
    assert is_transient_telegram_error(
        TelegramRetryAfter(method=method, message="flood", retry_after=3)
    )
    assert not is_transient_telegram_error(TelegramEntityTooLarge(method=method, message="big"))
    assert not is_transient_telegram_error(RuntimeError("bug"))


async def test_real_bug_is_still_reported(monkeypatch):
    _patch_user(monkeypatch)
    deps = _deps()
    container = _Container(deps)
    event = _error_event(RuntimeError("boom"), callback=False)

    await ErrorMiddleware().middleware_logic(AsyncMock(), event, _data(container))

    deps[EventPublisher].publish.assert_awaited_once()
    assert isinstance(deps[EventPublisher].publish.await_args.args[0], ErrorEvent)


# --------------------------------------------------------------------------- 5. middleware order


def test_error_middleware_runs_inside_fresh_dishka_container():
    dispatcher = Dispatcher()
    setup_dialogs(dispatcher)  # same order as src.__main__.application
    setup_middlewares(dispatcher)
    setup_dishka(make_async_container(Provider()), dispatcher)
    setup_error_middleware(dispatcher)

    outer = list(dispatcher.errors.outer_middleware)
    kinds = [type(m) for m in outer]
    assert ErrorMiddleware in kinds and ContainerMiddleware in kinds
    assert kinds.index(ContainerMiddleware) < kinds.index(ErrorMiddleware)
    # ErrorMiddleware is no longer registered by setup_middlewares (before dishka).
    assert kinds.count(ErrorMiddleware) == 1


# --------------------------------------------------------------------------- 3. enable/disable 400


@pytest.mark.parametrize(
    ("method", "code", "message"),
    [
        ("enable_user", "A030", "User already enabled"),
        ("disable_user", "A029", "User already disabled"),
    ],
)
async def test_action_already_in_state_is_idempotent(method, code, message):
    service, panel = _service(400, {"message": message, "statusCode": 400, "errorCode": code})
    await getattr(service, method)(7)
    assert len(panel.calls) == 1


async def test_action_other_400_raises_clear_error():
    service, _ = _service(
        400, {"message": "User already disabled", "statusCode": 400, "errorCode": "A029"}
    )
    with pytest.raises(RemnawaveActionError) as info:
        await service.enable_user(7)  # A029 on enable is not "already enabled"
    assert isinstance(info.value, ValueError)
    assert info.value.code == "A029"
    assert info.value.message == "User already disabled"
    assert info.value.action == "enable"


async def test_action_success_and_non_400_errors_unchanged():
    service, _ = _service(200, {"response": {}})
    await service.enable_user(7)
    service, _ = _service(500, {"message": "Enable user error", "errorCode": "A031"})
    with pytest.raises(httpx.HTTPStatusError):
        await service.enable_user(7)


# --------------------------------------------------------------------------- 4. apply_sync id


def test_apply_sync_never_overwrites_subscription_primary_key(sample_subscription_dto):
    subscription = sample_subscription_dto
    local_id = subscription.id
    remna_id = local_id + 1000
    assert "id" not in subscription.changed_data
    source = RemnaSubscriptionDto(
        id=remna_id,
        status=SubscriptionStatus.ACTIVE,
        expire_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        url="https://sub.example/x",
        traffic_limit=5,
        device_limit=2,
        traffic_limit_strategy=TrafficLimitStrategy.NO_RESET,
    )

    result = RemnawaveImpl(sdk=MagicMock()).apply_sync(subscription, source)

    assert result.id == local_id
    assert result.user_remna_id == remna_id
    assert "id" not in result.changed_data


# --------------------------------------------------------------------------- 6. translator


@pytest.mark.parametrize("value", ["space", "unit-day", "ntf-error.unknown", "btn-back.main_menu"])
def test_real_keys_look_like_keys(value):
    assert _looks_like_i18n_key(value)


@pytest.mark.parametrize("value", ["Standart", "Extended", "XL", "IMPORTED", "Тариф", "Pro 30"])
def test_raw_names_do_not_look_like_keys(value):
    assert not _looks_like_i18n_key(value)


def _runner() -> TranslatorRunnerImpl:
    runner = TranslatorRunnerImpl(translators=[], retort=Retort())

    def _missing(key: str, **kwargs: Any) -> str:
        raise KeyNotFoundError(key)

    runner._get_translation = _missing  # type: ignore[method-assign]
    return runner


def test_raw_plan_name_rendered_without_warning():
    records, sink_id = _capture_logs()
    try:
        assert _runner().get("Standart") == "Standart"
        # Events pass plan names as (name, {}) tuples, e.g. plan_name=(plan.name, {}).
        assert _runner()._translate_values({"plan_name": ("XL", {})}) == {"plan_name": "XL"}
    finally:
        logger.remove(sink_id)
    assert not any(level == "WARNING" for level, _ in records)


def test_missing_real_key_still_warns():
    records, sink_id = _capture_logs()
    try:
        assert _runner().get("ntf-missing.key") == "ntf-missing.key"
    finally:
        logger.remove(sink_id)
    assert any(level == "WARNING" and "ntf-missing.key" in msg for level, msg in records)


# --------------------------------------------------------------------------- 7. error report


def test_error_report_filename_and_header():
    event = ErrorEvent(
        event_id=UUID("30549654-2cd5-4a3e-bded-38546ba27988"),
        occurred_at=datetime(2026, 9, 15, 7, 3, 20, tzinfo=timezone.utc),
        exception=UnknownIntent("Context not found for intent id: wBHG99"),
    )

    filename, content = build_error_report(event, "log line", "Traceback ...")

    assert filename == "error_2026-09-15_07-03-20_30549654.txt"
    assert re.fullmatch(r"error_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_[0-9a-f]{8}\.txt", filename)
    lines = content.splitlines()
    assert lines[0] == "Time: 2026-09-15 07:03:20 UTC"
    assert lines[2] == "Exception: UnknownIntent: Context not found for intent id: wBHG99"
    assert content.index("Exception:") < content.index("=== LOG CONTEXT")
    assert content.endswith("Traceback ...")
