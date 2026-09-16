from aiogram import Router

from src.core.enums import MiddlewareEventType

from .access import AccessMiddleware
from .base import EventTypedMiddleware
from .channel import ChannelMiddleware
from .error import ErrorMiddleware
from .garbage import GarbageMiddleware
from .rules import RulesMiddleware
from .throttling import ThrottlingMiddleware
from .user import UserMiddleware

__all__ = [
    "setup_error_middleware",
    "setup_middlewares",
]


def setup_error_middleware(router: Router) -> None:
    """Register every middleware of the `errors` observer (UserMiddleware, ErrorMiddleware).

    Call AFTER dishka's `setup_dishka`: outer middlewares run in registration order, so dishka's
    ContainerMiddleware then opens a fresh request container (closed after the error is handled)
    before these middlewares resolve dependencies. Registered earlier, they would use the
    update-level container that aiogram's ErrorsMiddleware has already closed, leaking DB
    sessions/connections on every handled error.
    """
    UserMiddleware().setup_outer(router=router, event_types=[MiddlewareEventType.ERROR])
    ErrorMiddleware().setup_outer(router=router)


def setup_middlewares(router: Router) -> None:
    # The `errors` observer is wired separately by setup_error_middleware (after dishka).
    outer_middlewares: list[EventTypedMiddleware] = [
        AccessMiddleware(),
        UserMiddleware(),
        # Throttle before the heavier Rules/Channel checks (DB/Telegram API) so flood
        # is short-circuited early.
        ThrottlingMiddleware(),
        RulesMiddleware(),
        ChannelMiddleware(),
    ]

    inner_middlewares: list[EventTypedMiddleware] = [
        GarbageMiddleware(),
    ]

    for middleware in outer_middlewares:
        middleware.setup_outer(
            router=router,
            event_types=[t for t in middleware.__event_types__ if t != MiddlewareEventType.ERROR],
        )

    for middleware in inner_middlewares:
        middleware.setup_inner(router=router)
