"""The scrape endpoint and the second ASGI server that serves it.

The exporter never shares a port with the webhook application: it runs its own
`uvicorn.Server` inside a task owned by the caller (the FastAPI lifespan, or the taskiq worker
startup/shutdown hooks). Signal handling is disabled on that server so the embedded instance
cannot steal SIGINT/SIGTERM from the process that owns it.
"""

import asyncio
import socket
from typing import Any, Awaitable, Callable, Optional

import uvicorn
from loguru import logger
from prometheus_client import CollectorRegistry
from prometheus_client.exposition import choose_encoder

from src.core.config.metrics import MetricsConfig

from .registry import REGISTRY

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]

_NOT_FOUND = b"Not Found\n"


def build_metrics_app(
    path: str,
    registry: CollectorRegistry = REGISTRY,
    before_scrape: Optional[Callable[[], Awaitable[None]]] = None,
) -> Callable[[Scope, Receive, Send], Awaitable[None]]:
    """A dependency-free ASGI app exposing `path` (and `/` as a liveness probe).

    `before_scrape` may refresh cached data; it is expected to swallow its own errors, and any
    that escape are logged here so rendering still happens.
    """

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await _handle_lifespan(receive, send)
            return
        if scope["type"] != "http":  # pragma: no cover - websockets are never routed here
            return

        if scope.get("path", "/").rstrip("/") not in ("", path.rstrip("/")):
            await _respond(send, 404, b"text/plain; charset=utf-8", _NOT_FOUND)
            return

        if scope.get("path", "/").rstrip("/") == "" and path.rstrip("/") != "":
            await _respond(send, 200, b"text/plain; charset=utf-8", b"ok\n")
            return

        if before_scrape is not None:
            try:
                await before_scrape()
            except Exception as e:  # pragma: no cover - the provider swallows its own errors
                logger.warning(f"Metrics pre-scrape hook failed: '{e}'")

        encoder, content_type = choose_encoder(_accept_header(scope))
        try:
            payload = encoder(registry)
        except Exception as e:
            # A broken collector must never take the endpoint down.
            logger.exception(f"Failed to render Prometheus metrics: '{e}'")
            await _respond(send, 500, b"text/plain; charset=utf-8", b"collection failed\n")
            return

        await _respond(send, 200, content_type.encode("latin-1"), payload)

    return app


def _accept_header(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name.lower() == b"accept":
            return str(value.decode("latin-1"))
    return ""


async def _handle_lifespan(receive: Receive, send: Send) -> None:
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


async def _respond(send: Send, status: int, content_type: bytes, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", content_type),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class _EmbeddedUvicornServer(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        # The owning process (uvicorn for the webhook app, taskiq for the worker) handles
        # signals; a second handler would swallow the shutdown of the first.
        return None


class MetricsServer:
    """Runs `build_metrics_app` on its own port for as long as the owner keeps it started."""

    def __init__(
        self,
        config: MetricsConfig,
        before_scrape: Optional[Callable[[], Awaitable[None]]] = None,
        registry: CollectorRegistry = REGISTRY,
    ) -> None:
        self._config = config
        self._app = build_metrics_app(
            path=config.normalized_path,
            registry=registry,
            before_scrape=before_scrape,
        )
        self._server: Optional[_EmbeddedUvicornServer] = None
        self._task: Optional[asyncio.Task[None]] = None
        self._socket: Optional[socket.socket] = None

    @property
    def started(self) -> bool:
        return self._task is not None

    async def start(self) -> None:
        if self._task is not None:
            return

        # Bind here instead of letting uvicorn do it: uvicorn calls `sys.exit(1)` on a bind
        # error, which inside a task would surface as an unretrieved SystemExit. A port that
        # is already taken (e.g. a second forked taskiq worker process) must only cost the
        # exporter, never the process that owns it.
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self._config.host, self._config.port))
            sock.listen(128)
            sock.set_inheritable(True)
        except OSError as e:
            logger.warning(
                f"Metrics exporter could not bind "
                f"'{self._config.host}:{self._config.port}': '{e}'; metrics are not served "
                f"from this process"
            )
            return

        uvicorn_config = uvicorn.Config(
            app=self._app,
            log_level="warning",
            access_log=False,
            lifespan="off",
        )
        self._server = _EmbeddedUvicornServer(uvicorn_config)
        self._socket = sock
        self._task = asyncio.create_task(
            self._server.serve(sockets=[sock]),
            name="remnashop-metrics-server",
        )
        logger.info(
            f"Metrics exporter listening on "
            f"'{self._config.host}:{self._config.port}{self._config.normalized_path}'"
        )

    @property
    def port(self) -> Optional[int]:
        """The bound port; useful when the configured port is 0 (tests)."""
        if self._socket is None:
            return None
        return int(self._socket.getsockname()[1])

    async def stop(self) -> None:
        server, task, sock = self._server, self._task, self._socket
        self._server, self._task, self._socket = None, None, None
        if server is None or task is None:
            if sock is not None:
                sock.close()
            return

        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=10)
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("Metrics exporter did not stop in time, cancelling")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except asyncio.CancelledError:  # pragma: no cover - propagated shutdown
            raise
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"Metrics exporter stopped with an error: '{e}'")
        finally:
            try:
                if sock is not None:
                    sock.close()
            except Exception:  # pragma: no cover - uvicorn usually closed it already
                pass
        logger.info("Metrics exporter stopped")
