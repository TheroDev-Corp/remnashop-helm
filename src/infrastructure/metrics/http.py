"""Pure-ASGI HTTP instrumentation for the FastAPI application.

Labels carry the *route template* (`/api/v1/payments/{gateway_type}`), never the raw URL, so a
flood of webhook calls cannot multiply time series. Requests that match no route collapse into
a single `__unmatched__` series.
"""

import time
from typing import Any, Callable, Optional

from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .registry import (
    HTTP_EXCEPTIONS,
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    HTTP_REQUESTS_IN_PROGRESS,
)

UNMATCHED: str = "__unmatched__"


def resolve_route_path(routes: Any, scope: Scope) -> str:
    """The template of the route that handled `scope`, or `__unmatched__`."""
    try:
        for route in routes:
            match, _ = route.matches(scope)
            if match == Match.FULL:
                path = getattr(route, "path", None)
                if isinstance(path, str) and path:
                    return path
                return UNMATCHED
    except Exception:  # pragma: no cover - never let bookkeeping break a request
        return UNMATCHED
    return UNMATCHED


class PrometheusHttpMiddleware:
    def __init__(self, app: ASGIApp, routes_getter: Callable[[], Any]) -> None:
        self.app = app
        self._routes_getter = routes_getter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        path = resolve_route_path(self._routes_getter(), scope)
        status_code: Optional[int] = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        started = time.perf_counter()
        in_progress = HTTP_REQUESTS_IN_PROGRESS.labels(method, path)
        in_progress.inc()
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            HTTP_EXCEPTIONS.labels(method, path, type(e).__name__).inc()
            status_code = status_code or 500
            raise
        finally:
            in_progress.dec()
            HTTP_REQUEST_DURATION.labels(method, path).observe(time.perf_counter() - started)
            HTTP_REQUESTS.labels(method, path, str(status_code or 500)).inc()


def setup_http_metrics(app: Any) -> None:
    """Install the middleware outermost so it also sees responses produced by other middleware."""
    app.add_middleware(PrometheusHttpMiddleware, routes_getter=lambda: app.router.routes)


__all__ = [
    "PrometheusHttpMiddleware",
    "UNMATCHED",
    "resolve_route_path",
    "setup_http_metrics",
]
