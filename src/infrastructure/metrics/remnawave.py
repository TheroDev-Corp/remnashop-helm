"""Remnawave panel API instrumentation.

Wraps the httpx transport of the SDK client, so both responses and transport failures (which
never reach an httpx response event hook) are counted, with the exact call duration.

Endpoint labels are templates: numeric ids, UUIDs and lookup values are replaced with
placeholders, so `/users/12345` and `/users/67890` share one series.
"""

import re
import time
from typing import Final, Optional

import httpx

from .registry import (
    REMNAWAVE_ERRORS,
    REMNAWAVE_REQUEST_DURATION,
    REMNAWAVE_REQUESTS,
    set_panel_available,
)

_UUID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
# Segments whose *next* segment is a user-supplied lookup value rather than a sub-resource.
_LOOKUP_SEGMENTS: Final[dict[str, str]] = {
    "by-username": "{username}",
    "by-email": "{email}",
    "by-telegram-id": "{telegram_id}",
    "by-tag": "{tag}",
}
_MAX_SEGMENTS: Final[int] = 8


def normalize_endpoint(path: str) -> str:
    """`/api/users/42/actions/enable` -> `/users/{id}/actions/enable`."""
    cleaned = path.split("?", 1)[0]
    if cleaned.startswith("/api/"):
        cleaned = cleaned[4:]
    elif cleaned == "/api":
        cleaned = "/"

    segments = [s for s in cleaned.split("/") if s]
    if not segments:
        return "/"

    normalized: list[str] = []
    placeholder: Optional[str] = None
    for segment in segments[:_MAX_SEGMENTS]:
        if placeholder is not None:
            normalized.append(placeholder)
            placeholder = None
            continue
        if segment.isdigit() or _UUID_RE.match(segment):
            normalized.append("{id}")
            continue
        normalized.append(segment)
        placeholder = _LOOKUP_SEGMENTS.get(segment)

    return "/" + "/".join(normalized)


class MetricsTransport(httpx.AsyncBaseTransport):
    """Counts panel calls, their duration and the last observed panel availability."""

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self._transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        endpoint = normalize_endpoint(request.url.path)
        method = request.method.upper()
        started = time.perf_counter()
        try:
            response = await self._transport.handle_async_request(request)
        except Exception as e:
            REMNAWAVE_REQUEST_DURATION.labels(method, endpoint).observe(
                time.perf_counter() - started
            )
            REMNAWAVE_ERRORS.labels(endpoint, type(e).__name__).inc()
            set_panel_available(False)
            raise

        REMNAWAVE_REQUEST_DURATION.labels(method, endpoint).observe(time.perf_counter() - started)
        REMNAWAVE_REQUESTS.labels(method, endpoint, str(response.status_code)).inc()
        set_panel_available(response.status_code < 500)
        return response

    async def aclose(self) -> None:
        await self._transport.aclose()


def build_instrumented_transport(*, verify: bool = True) -> httpx.AsyncBaseTransport:
    return MetricsTransport(httpx.AsyncHTTPTransport(verify=verify))
