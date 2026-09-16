# ruff: noqa: PLC0415
"""Boot the real bot (`src.__main__.application()` + `src.lifespan`) with faked boundaries.

Real: AppConfig, Dispatcher (routers, dialogs, middlewares, global filters), aiogram-dialog
setup with the project MessageManager, every Dishka provider (DAOs, use cases, services,
i18n, retort, payment gateway factory, database engine against a throwaway Postgres), the
FastAPI app and the startup lifespan (default gateways/settings, webhook + commands setup,
event bus autodiscovery, notification worker).

Faked: Telegram Bot API (FakeTelegramSession), Redis (fakeredis for both FSM storage and the
app client), Remnawave panel (FakePanel behind httpx.MockTransport + typed SDK stubs).
"""

from __future__ import annotations

import asyncio
import itertools
import traceback
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Optional

import fakeredis
import httpx
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from loguru import logger
from redis.asyncio import Redis
from remnapy import RemnawaveSDK
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.core.config import AppConfig

from .fake_panel import FakePanel, install_sdk_stubs
from .fake_telegram import FakeTelegramSession


@dataclass
class RecordedError:
    source: str
    exception: Optional[BaseException]
    text: str


class _NeverCache(dict):
    """Drop-in for ThrottlingMiddleware's TTLCache: nobody is ever throttled."""

    def __contains__(self, key: object) -> bool:
        return False

    def __setitem__(self, key: Any, value: Any) -> None:
        return None


class SmokeProvider(Provider):
    scope = Scope.APP

    def __init__(
        self,
        session: FakeTelegramSession,
        panel: FakePanel,
        redis_server: fakeredis.FakeServer,
    ) -> None:
        super().__init__()
        self._session = session
        self._panel = panel
        self._redis_server = redis_server

    @provide
    async def get_redis(self) -> AsyncIterable[Redis]:
        client = fakeredis.FakeAsyncRedis(server=self._redis_server, decode_responses=True)
        yield client
        await client.aclose()

    @provide
    async def get_remnawave_sdk(self) -> AsyncIterator[RemnawaveSDK]:
        client = httpx.AsyncClient(
            base_url="https://panel.smoke.test/api", transport=self._panel.transport()
        )
        sdk = RemnawaveSDK(client)
        install_sdk_stubs(sdk, self._panel)
        try:
            yield sdk
        finally:
            await client.aclose()


def _make_provider(
    session: FakeTelegramSession, panel: FakePanel, redis_server: fakeredis.FakeServer
) -> Provider:
    class _Provider(SmokeProvider):
        @provide
        async def get_bot(self, config: AppConfig) -> AsyncIterable[Bot]:  # type: ignore[override]
            async with Bot(
                token=config.bot.token.get_secret_value(),
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
                session=session,
            ) as bot:
                yield bot

    return _Provider(session, panel, redis_server)


class SmokeApp:
    IGNORED_TASKS = (
        "NotificationQueue._worker",
        "NotificationService._schedule_message_deletion",
    )

    def __init__(self) -> None:
        self.session = FakeTelegramSession()
        self.panel = FakePanel()
        self.redis_server = fakeredis.FakeServer()
        self.errors: list[RecordedError] = []
        self.expected_errors: list[RecordedError] = []
        self.leaked_transactions: list[Any] = []
        self._expect: tuple[type[BaseException], ...] = ()
        self.update_ids = itertools.count(1)
        self._lifespan: Optional[AbstractAsyncContextManager[None]] = None
        self._log_sink_id: Optional[int] = None
        self.container: AsyncContainer
        self.bot: Bot

    # ------------------------------------------------------------------ assembly
    async def start(self) -> None:
        import src.__main__ as main_module
        import src.infrastructure.di.ioc as ioc_module
        from src.lifespan import lifespan
        from src.telegram import dispatcher as dispatcher_module
        from src.telegram.middlewares.throttling import ThrottlingMiddleware

        server = self.redis_server

        class _FakeRedisStorage(RedisStorage):
            @classmethod
            def from_url(cls, url: str, connection_kwargs: Any = None, **kwargs: Any) -> Any:
                return cls(redis=fakeredis.FakeAsyncRedis(server=server), **kwargs)

        self._log_sink_id = logger.add(self._log_sink, level="ERROR", backtrace=False)
        asyncio.get_running_loop().set_exception_handler(self._loop_exception_handler)
        self._install_process_patches()

        # Run the real `src.__main__.application()` so the harness can never drift from the
        # production wiring order; only external boundaries are swapped for its duration.
        smoke_provider = _make_provider(self.session, self.panel, self.redis_server)
        captured: dict[str, Any] = {}
        original_get_dispatcher = main_module.get_dispatcher
        original_get_bg = main_module.get_bg_manager_factory

        def get_dispatcher_with_recorder(config: AppConfig) -> Any:
            dispatcher = original_get_dispatcher(config)
            # Outermost error observer: sees every exception before project middlewares.
            dispatcher.errors.outer_middleware(self._error_recorder)
            captured["dispatcher"] = dispatcher
            return dispatcher

        def get_bg_manager_factory(dispatcher: Any) -> Any:
            captured["bg"] = original_get_bg(dispatcher)
            return captured["bg"]

        def make_container_with_fakes(*providers: Any, **kwargs: Any) -> AsyncContainer:
            return make_async_container(*providers, smoke_provider, **kwargs)

        patches = [
            (main_module, "setup_logger", lambda config: None),
            (main_module, "get_dispatcher", get_dispatcher_with_recorder),
            (main_module, "get_bg_manager_factory", get_bg_manager_factory),
            (dispatcher_module, "RedisStorage", _FakeRedisStorage),
            (ioc_module, "make_async_container", make_container_with_fakes),
        ]
        originals = [(obj, name, getattr(obj, name)) for obj, name, _ in patches]
        for obj, name, value in patches:
            setattr(obj, name, value)
        try:
            app = main_module.application()
        finally:
            for obj, name, value in originals:
                setattr(obj, name, value)

        config = AppConfig.get()
        dispatcher = captured["dispatcher"]
        bg_manager_factory = captured["bg"]
        container = app.state.dishka_container

        # Consecutive clicks inside 0.5s would be throttled; users in tests click instantly.
        for observer in dispatcher.observers.values():
            for mw in getattr(observer.outer_middleware, "_middlewares", []):
                if isinstance(mw, ThrottlingMiddleware):
                    mw.cache = _NeverCache()  # type: ignore[assignment]

        self.config = config
        self.dispatcher = dispatcher
        self.bg_manager_factory = bg_manager_factory
        self.fastapi = app
        self.container = container
        self.bot = await container.get(Bot)

        self._lifespan = lifespan(app)
        await self._lifespan.__aenter__()
        await self.settle()

    def _install_process_patches(self) -> None:
        """Observe renders and swallow taskiq enqueues (no broker in tests)."""
        from aiogram_dialog.window import Window as BaseWindow
        from taskiq.kicker import AsyncKicker

        self.renders: list[tuple[int, str]] = []
        self.kiq_calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        original_render = BaseWindow.render
        original_kiq = AsyncKicker.kiq
        smoke = self

        async def render(window: Any, dialog: Any, manager: Any) -> Any:
            try:
                chat = manager.middleware_data["event_chat"].id
                smoke.renders.append((int(chat), manager.current_context().state.state))
            except Exception:
                pass
            return await original_render(window, dialog, manager)

        async def kiq(kicker: Any, *args: Any, **kwargs: Any) -> Any:
            from types import SimpleNamespace

            task_name = str(getattr(kicker, "task_name", "?"))
            smoke.kiq_calls.append((task_name, args, kwargs))

            async def wait_result(*_: Any, **__: Any) -> Any:
                # Run what the worker would run, in-process, so callers get a real result.
                from src.infrastructure.taskiq.tasks import importer as importer_tasks

                value: Any = None
                use_case = None
                if task_name.endswith("sync_all_users_from_panel_task"):
                    use_case = importer_tasks.SyncAllUsersFromPanel
                elif task_name.endswith("sync_all_users_from_bot_task"):
                    use_case = importer_tasks.SyncAllUsersFromBot
                elif task_name.endswith("import_exported_users_task"):
                    value = (len(args[0]) if args else 0, 0)
                if use_case is not None:
                    async with smoke.container(scope=Scope.REQUEST) as request:
                        value = await (await request.get(use_case)).system()
                return SimpleNamespace(is_err=False, return_value=value, error=None)

            return SimpleNamespace(task_id="smoke-task", wait_result=wait_result)

        BaseWindow.render = render  # type: ignore[method-assign]
        AsyncKicker.kiq = kiq  # type: ignore[method-assign]

        # Production writes logs/bot.log through setup_logger(); tests don't configure file
        # logging, so give the "logs" admin button a real file in a temp dir instead.
        import tempfile
        from pathlib import Path

        import src.application.use_cases.misc.queries.logs as logs_module
        from src.core.logger import LOG_FILENAME

        self._log_dir = Path(tempfile.mkdtemp(prefix="smoke-logs-"))
        (self._log_dir / LOG_FILENAME).write_text("smoke log line\n", "utf8")
        original_log_dir = logs_module.LOG_DIR
        logs_module.LOG_DIR = self._log_dir  # type: ignore[misc]

        def restore() -> None:
            BaseWindow.render = original_render  # type: ignore[method-assign]
            AsyncKicker.kiq = original_kiq  # type: ignore[method-assign]
            logs_module.LOG_DIR = original_log_dir  # type: ignore[misc]

        self._restore_patches = restore

    def current_state(self, chat_id: int) -> Optional[str]:
        for chat, state in reversed(self.renders):
            if chat == chat_id:
                return state
        return None

    async def stop(self) -> None:
        try:
            if self._lifespan is not None:
                await self._lifespan.__aexit__(None, None, None)
        finally:
            if self._log_sink_id is not None:
                logger.remove(self._log_sink_id)
            restore = getattr(self, "_restore_patches", None)
            if restore:
                restore()

    # ------------------------------------------------------------------ dialog snapshots
    async def snapshot_dialog(self, user_id: int) -> tuple[dict[str, Any], dict[int, Any]]:
        client = fakeredis.FakeAsyncRedis(server=self.redis_server)
        keys = list(await client.keys(f"fsm:*:{user_id}:{user_id}:*"))
        values = {k: await client.dump(k) for k in keys}
        await client.aclose()
        return values, dict(self.session.chats.get(user_id, {}))

    async def restore_dialog(
        self, user_id: int, snapshot: tuple[dict[str, Any], dict[int, Any]]
    ) -> None:
        values, messages = snapshot
        client = fakeredis.FakeAsyncRedis(server=self.redis_server)
        for key in await client.keys(f"fsm:*:{user_id}:{user_id}:*"):
            await client.delete(key)
        for key, dumped in values.items():
            await client.restore(key, 0, dumped, replace=True)
        await client.aclose()
        self.session.chats[user_id] = dict(messages)

    # ------------------------------------------------------------------ error capture
    async def _error_recorder(self, handler: Any, event: Any, data: dict[str, Any]) -> Any:
        exc = event.exception
        self._record("dispatcher.errors", exc, "".join(traceback.format_exception(exc)))
        return await handler(event, data)

    def _log_sink(self, message: Any) -> None:
        record = message.record
        exc_text = ""
        if record["exception"] is not None:
            exc = record["exception"]
            exc_text = "".join(traceback.format_exception(exc.type, exc.value, exc.traceback))
            exc_value = exc.value
        else:
            exc_value = None
        self._record(
            f"log:{record['name']}:{record['function']}:{record['line']}",
            exc_value,
            f"{record['message']}\n{exc_text}",
        )

    def _loop_exception_handler(self, loop: Any, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        tb = "".join(traceback.format_exception(exc)) if exc else ""
        self._record("asyncio", exc, f"{context.get('message')}\n{tb}")

    def _record(self, source: str, exc: Optional[BaseException], text_: str) -> None:
        item = RecordedError(source, exc, text_)
        if self._expect and exc is not None and isinstance(exc, self._expect):
            self.expected_errors.append(item)
        elif (
            self._expect
            and source.startswith("log:")
            and any(e.__name__ in text_ for e in self._expect)
        ):
            self.expected_errors.append(item)
        else:
            self.errors.append(item)

    def expect_errors(self, *exc_types: type[BaseException]) -> "_Expect":
        return _Expect(self, exc_types)

    def take_errors(self) -> list[RecordedError]:
        errors, self.errors = self.errors, []
        return errors

    def assert_no_errors(self, context: str = "") -> None:
        errors = self.take_errors()
        if errors:
            details = "\n\n".join(f"[{e.source}]\n{e.text}" for e in errors)
            raise AssertionError(f"Unhandled errors during {context or 'update'}:\n{details}")

    # ------------------------------------------------------------------ async settling
    async def settle(self, timeout: float = 15.0) -> None:
        """Wait for background work spawned by an update (bg dialog updates, event bus)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        current = asyncio.current_task()
        while True:
            for _ in range(10):
                await asyncio.sleep(0)
            pending = [
                t
                for t in asyncio.all_tasks()
                if t is not current and not t.done() and not self._is_ignored(t)
            ]
            if not pending:
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AssertionError(f"Background tasks did not finish: {pending}")
            await asyncio.wait(pending, timeout=min(remaining, 0.5))

    def _is_ignored(self, task: asyncio.Task[Any]) -> bool:
        coro = task.get_coro()
        name = getattr(coro, "__qualname__", "") or ""
        if any(name.endswith(ignored) for ignored in self.IGNORED_TASKS):
            return True
        # pytest-asyncio / anyio runner internals live outside the project.
        module = getattr(getattr(coro, "cr_frame", None), "f_globals", {}).get("__name__", "")
        return not (
            module.startswith("src.") or module.startswith("aiogram") or module.startswith("dishka")
        )

    # ------------------------------------------------------------------ database helpers
    async def truncate_all(self) -> None:
        engine = await self.container.get(AsyncEngine)
        # Background workers (notification queue) may be mid-transaction; give them time to
        # finish. Anything still inside a transaction after the grace period is a real leak.
        stuck: list[Any] = []
        for _ in range(50):
            async with engine.connect() as conn:
                activity = await conn.execute(
                    text(
                        "SELECT pid, state, now() - xact_start AS age, left(query, 300) "
                        "FROM pg_stat_activity WHERE datname = current_database() "
                        "AND pid <> pg_backend_pid() AND xact_start IS NOT NULL"
                    )
                )
                stuck = list(activity)
            if not stuck:
                break
            await asyncio.sleep(0.1)
        if stuck:
            previous = getattr(self, "current_test", "<startup>")
            self.leaked_transactions.extend((*row, previous) for row in stuck)
            async with engine.connect() as conn:
                for row in stuck:
                    await conn.execute(text(f"SELECT pg_terminate_backend({int(row[0])})"))
                await conn.commit()
        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            rows = await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                    "AND tablename <> 'alembic_version'"
                )
            )
            tables = [f'"{r[0]}"' for r in rows]
            if tables:
                await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))

    async def flush_redis(self) -> None:
        client = fakeredis.FakeAsyncRedis(server=self.redis_server)
        await client.flushall()
        await client.aclose()


class _Expect:
    def __init__(self, app: SmokeApp, exc_types: tuple[type[BaseException], ...]) -> None:
        self.app = app
        self.exc_types = exc_types

    def __enter__(self) -> list[RecordedError]:
        self.app._expect = self.exc_types
        self.app.expected_errors = []
        return self.app.expected_errors

    def __exit__(self, *exc: Any) -> None:
        self.app._expect = ()
