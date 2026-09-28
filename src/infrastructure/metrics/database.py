"""SQLAlchemy pool and statement instrumentation.

Pool size / checked-out / overflow are read live from the engine at scrape time (a collector,
so a disposed-and-recreated pool is picked up automatically). Everything else rides on engine
and pool events, plus one wrapper around `Pool.connect` — the only place where the time a
caller *waits* for a connection is observable.
"""

import time
from collections.abc import Iterator
from typing import Any, Final, Optional

from loguru import logger
from prometheus_client.metrics_core import GaugeMetricFamily, Metric
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from .registry import (
    DB_CONNECTION_USAGE,
    DB_ERRORS,
    DB_POOL_EVENTS,
    DB_POOL_WAIT,
    DB_QUERY_DURATION,
    REGISTRY,
)

_CHECKOUT_KEY: Final[str] = "remnashop_metrics_checkout_at"
_INSTRUMENTED: Final[str] = "_remnashop_metrics_instrumented"


class PoolCollector:
    """Live pool gauges; yields nothing while no engine is registered."""

    def __init__(self) -> None:
        self._engine: Optional[Engine] = None

    def set_engine(self, engine: Optional[Engine]) -> None:
        self._engine = engine

    def describe(self) -> list[Metric]:
        return []

    def collect(self) -> Iterator[Metric]:
        engine = self._engine
        if engine is None:
            return
        try:
            pool = engine.pool
            size = int(getattr(pool, "size", lambda: 0)())
            checked_in = int(getattr(pool, "checkedin", lambda: 0)())
            checked_out = int(getattr(pool, "checkedout", lambda: 0)())
            overflow = int(getattr(pool, "overflow", lambda: 0)())
        except Exception:  # pragma: no cover - pool internals differ between dialects
            return

        yield GaugeMetricFamily(
            "remnashop_db_pool_size",
            "Configured size of the SQLAlchemy connection pool",
            value=size,
        )
        connections = GaugeMetricFamily(
            "remnashop_db_pool_connections",
            "Pooled connections by state",
            labels=["state"],
        )
        connections.add_metric(["idle"], checked_in)
        connections.add_metric(["in_use"], checked_out)
        yield connections
        yield GaugeMetricFamily(
            "remnashop_db_pool_overflow",
            "Connections opened beyond the configured pool size "
            "(negative until the pool is full, as SQLAlchemy reports it)",
            value=overflow,
        )


POOL_COLLECTOR: Final[PoolCollector] = PoolCollector()
REGISTRY.register(POOL_COLLECTOR)  # type: ignore[arg-type]


def instrument_engine(engine: AsyncEngine) -> None:
    """Attach pool/statement listeners to `engine`. Safe to call once per engine."""
    sync_engine = engine.sync_engine
    if getattr(sync_engine, _INSTRUMENTED, False):
        return
    setattr(sync_engine, _INSTRUMENTED, True)

    POOL_COLLECTOR.set_engine(sync_engine)
    _listen_pool_events(sync_engine)
    _listen_statement_events(sync_engine)
    _instrument_pool_wait(sync_engine)
    logger.debug("SQLAlchemy engine instrumented for Prometheus metrics")


def _listen_pool_events(sync_engine: Engine) -> None:
    @event.listens_for(sync_engine, "connect")
    def _on_connect(dbapi_connection: Any, connection_record: Any) -> None:
        DB_POOL_EVENTS.labels("connect").inc()

    @event.listens_for(sync_engine, "close")
    def _on_close(dbapi_connection: Any, connection_record: Any) -> None:
        DB_POOL_EVENTS.labels("close").inc()

    @event.listens_for(sync_engine, "invalidate")
    def _on_invalidate(dbapi_connection: Any, connection_record: Any, exception: Any) -> None:
        DB_POOL_EVENTS.labels("invalidate").inc()
        if exception is not None:
            DB_ERRORS.labels(type(exception).__name__).inc()

    @event.listens_for(sync_engine, "soft_invalidate")
    def _on_soft_invalidate(dbapi_connection: Any, connection_record: Any, exception: Any) -> None:
        DB_POOL_EVENTS.labels("soft_invalidate").inc()

    @event.listens_for(sync_engine, "checkout")
    def _on_checkout(dbapi_connection: Any, connection_record: Any, connection_proxy: Any) -> None:
        DB_POOL_EVENTS.labels("checkout").inc()
        connection_record.info[_CHECKOUT_KEY] = time.perf_counter()

    @event.listens_for(sync_engine, "checkin")
    def _on_checkin(dbapi_connection: Any, connection_record: Any) -> None:
        DB_POOL_EVENTS.labels("checkin").inc()
        started = connection_record.info.pop(_CHECKOUT_KEY, None)
        if started is not None:
            DB_CONNECTION_USAGE.observe(time.perf_counter() - started)


def _listen_statement_events(sync_engine: Engine) -> None:
    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before_cursor_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        conn.info.setdefault("remnashop_metrics_query_start", []).append(time.perf_counter())

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after_cursor_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        stack = conn.info.get("remnashop_metrics_query_start")
        if stack:
            DB_QUERY_DURATION.observe(time.perf_counter() - stack.pop())

    @event.listens_for(sync_engine, "handle_error")
    def _handle_error(context: Any) -> None:
        exception = getattr(context, "original_exception", None)
        DB_ERRORS.labels(type(exception).__name__ if exception else "UnknownError").inc()


def _instrument_pool_wait(sync_engine: Engine) -> None:
    """Time `Pool.connect()`, i.e. the wait for a free connection, and count its failures.

    SQLAlchemy exposes no event around the wait itself (`checkout` already has the connection
    in hand and a pool timeout never reaches `handle_error`), so the pool's bound method is
    wrapped instead. Wrapping is per-instance and confined to this function.
    """
    pool = sync_engine.pool
    if getattr(pool, _INSTRUMENTED, False):
        return

    original_connect = pool.connect

    def timed_connect() -> Any:
        started = time.perf_counter()
        try:
            connection = original_connect()
        except Exception as e:
            DB_ERRORS.labels(type(e).__name__).inc()
            DB_POOL_WAIT.observe(time.perf_counter() - started)
            raise
        DB_POOL_WAIT.observe(time.perf_counter() - started)
        return connection

    try:
        pool.connect = timed_connect  # type: ignore[method-assign]
        setattr(pool, _INSTRUMENTED, True)
    except Exception:  # pragma: no cover - defensive, never break engine creation
        logger.debug("Could not instrument pool wait time")


def reset_engine_instrumentation() -> None:
    """Drop the engine reference (used when the owning container is closed and in tests)."""
    POOL_COLLECTOR.set_engine(None)


__all__ = [
    "POOL_COLLECTOR",
    "PoolCollector",
    "instrument_engine",
    "reset_engine_instrumentation",
]
