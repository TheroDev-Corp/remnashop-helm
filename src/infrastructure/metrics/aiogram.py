"""Aiogram update instrumentation.

Registered on the `update` observer *before* dishka and the project middlewares, so it is the
outermost layer: it measures the whole pipeline and sees only the exceptions that the error
middleware did not handle. It resolves no dependencies and opens no scope, so it cannot
interfere with the container/middleware ordering that `setup_error_middleware` documents.
"""

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Dispatcher
from aiogram.types import TelegramObject, Update

from .registry import (
    TELEGRAM_UPDATE_DURATION,
    TELEGRAM_UPDATE_EXCEPTIONS,
    TELEGRAM_UPDATES,
)

UNKNOWN_UPDATE: str = "unknown"


def update_type(event: TelegramObject) -> str:
    if isinstance(event, Update):
        try:
            return str(event.event_type)
        except Exception:
            return UNKNOWN_UPDATE
    return type(event).__name__.lower()


class UpdateMetricsMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        kind = update_type(event)
        TELEGRAM_UPDATES.labels(kind).inc()
        started = time.perf_counter()
        try:
            return await handler(event, data)
        except Exception as e:
            TELEGRAM_UPDATE_EXCEPTIONS.labels(kind, type(e).__name__).inc()
            raise
        finally:
            TELEGRAM_UPDATE_DURATION.labels(kind).observe(time.perf_counter() - started)


def setup_update_metrics(dispatcher: Dispatcher) -> None:
    dispatcher.update.outer_middleware(UpdateMetricsMiddleware())
