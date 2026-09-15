# ruff: noqa: PLC0415
"""Fixtures for bot smoke tests.

Bot modules are imported lazily inside fixtures/tests (hence PLC0415 is disabled across
tests/smoke), so collecting the rest of the test suite never imports or boots the application.

A throwaway PostgreSQL 16 cluster is started from the `pgserver` dev dependency's bundled
binaries (no Docker needed), migrated with the project's Alembic migrations, and shared by the
whole session. Every test gets a truncated + re-seeded database, flushed fake Redis and fresh
fake Telegram/panel state, while the Dispatcher/container are assembled once per session
(module-level routers can only be attached to a single Dispatcher).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import AsyncIterator, Iterator

import pytest
import pytest_asyncio

REPO_ROOT = Path(__file__).resolve().parents[2]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "smoke: end-to-end bot smoke tests (real dispatcher, fake Telegram/panel)"
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)  # type: ignore[call-overload]


@pytest.fixture(scope="session")
def smoke_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    try:
        from pgserver._commands import POSTGRES_BIN_PATH  # type: ignore[import-untyped]
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"smoke tests need the 'pgserver' dev dependency for PostgreSQL: {e}")

    bin_dir = Path(POSTGRES_BIN_PATH)
    data_dir = tmp_path_factory.mktemp("pg") / "data"
    log_file = data_dir.parent / "postgres.log"
    port = _free_port()

    initdb = _run(
        [
            str(bin_dir / "initdb"),
            "-D",
            str(data_dir),
            "-U",
            "postgres",
            "--auth=trust",
            "-E",
            "UTF8",
            "--locale=C",
        ]
    )
    if initdb.returncode != 0:
        pytest.skip(f"initdb failed: {initdb.stderr}")

    options = (
        f"-h 127.0.0.1 -p {port} -c unix_socket_directories='' "
        "-c fsync=off -c synchronous_commit=off -c full_page_writes=off"
    )
    start = _run(
        [
            str(bin_dir / "pg_ctl"),
            "-D",
            str(data_dir),
            "-o",
            options,
            "-l",
            str(log_file),
            "-w",
            "start",
        ]
    )
    if start.returncode != 0:
        log = log_file.read_text() if log_file.exists() else ""
        pytest.skip(f"could not start PostgreSQL: {start.stderr}\n{log}")

    try:
        created = _run(
            [
                str(bin_dir / "createdb"),
                "-h",
                "127.0.0.1",
                "-p",
                str(port),
                "-U",
                "postgres",
                "remnashop_smoke",
            ]
        )
        assert created.returncode == 0, created.stderr

        os.environ.update(
            {
                # Backups shell out to pg_dump/psql, which the production image ships.
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "DATABASE_HOST": "127.0.0.1",
                "DATABASE_PORT": str(port),
                "DATABASE_NAME": "remnashop_smoke",
                "DATABASE_USER": "postgres",
                "DATABASE_PASSWORD": "postgres",
                "BOT_SETUP_COMMANDS": "true",
            }
        )
        migrate = _run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "src/infrastructure/database/alembic.ini",
                "upgrade",
                "head",
            ],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
        )
        if migrate.returncode != 0:
            pytest.fail(
                "Alembic migrations failed on a clean database:\n"
                f"{migrate.stdout}\n{migrate.stderr}"
            )
        yield f"postgresql+asyncpg://postgres@127.0.0.1:{port}/remnashop_smoke"
    finally:
        _run([str(bin_dir / "pg_ctl"), "-D", str(data_dir), "-m", "immediate", "stop"])


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def smoke_app_session(smoke_database: str) -> AsyncIterator[object]:
    from loguru import logger

    from tests.smoke.harness.app import SmokeApp

    # Project code logs a lot at DEBUG; keep only WARNING+ on stderr during smoke runs.
    try:
        logger.remove(0)
        restore_default = True
    except ValueError:
        restore_default = False
    stderr_sink = logger.add(sys.stderr, level="WARNING")

    app = SmokeApp()
    await app.start()
    startup_errors = app.take_errors()
    app.startup_errors = startup_errors  # type: ignore[attr-defined]
    app.startup_call_names = app.session.call_names()  # type: ignore[attr-defined]
    try:
        yield app
    finally:
        if app.leaked_transactions:
            sys.stdout.write("\n[smoke] connections left idle-in-transaction between tests:\n")
            for row in app.leaked_transactions:
                sys.stdout.write(
                    f"  after={row[4]} pid={row[0]} state={row[1]} age={row[2]} query={row[3]!r}\n"
                )
        await app.stop()
        logger.remove(stderr_sink)
        if restore_default:
            logger.add(sys.stderr)


@pytest_asyncio.fixture(loop_scope="session")
async def app(smoke_app_session: object, request: pytest.FixtureRequest) -> AsyncIterator[object]:
    from tests.smoke.harness.seed import seed

    smoke = smoke_app_session
    await smoke.settle()  # type: ignore[attr-defined]
    await smoke.truncate_all()  # type: ignore[attr-defined]
    smoke.current_test = request.node.nodeid  # type: ignore[attr-defined]
    await smoke.flush_redis()  # type: ignore[attr-defined]
    smoke.session.reset()  # type: ignore[attr-defined]
    smoke.panel.reset()  # type: ignore[attr-defined]
    smoke.take_errors()  # type: ignore[attr-defined]
    smoke.seed = await seed(smoke)  # type: ignore[attr-defined]
    smoke.assert_no_errors("seeding")  # type: ignore[attr-defined]
    yield smoke
    await smoke.settle()  # type: ignore[attr-defined]
    smoke.assert_no_errors("test teardown")  # type: ignore[attr-defined]
