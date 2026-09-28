# ruff: noqa: PLC0415
"""The Grafana dashboard against the real exporter: every series it plots must exist.

The second test goes further when Docker is available: a real VictoriaMetrics scrapes the
exporter and runs every dashboard query, so a PromQL typo or a renamed metric fails here
instead of showing up as an empty panel in production.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]

DASHBOARD = Path(__file__).parents[2] / "dashboards" / "remnashop.json"
VM_IMAGE = "victoriametrics/victoria-metrics:v1.106.1"
METRIC_RE = re.compile(r"\b((?:remnashop|process)_[a-z0-9_]+)")
SUFFIXES = ("_bucket", "_count", "_sum", "_total")
# Only move on errors or with a real taskiq broker (the harness runs tasks in-process).
MAY_BE_EMPTY = (
    "remnashop_telegram_update_exceptions",
    "remnashop_http_unhandled_exceptions",
    "remnashop_remnawave_errors",
    "remnashop_db_errors",
    "remnashop_taskiq_",
)


def _dashboard_exprs() -> list[tuple[str, str]]:
    dashboard = json.loads(DASHBOARD.read_text("utf8"))
    return [
        (panel["title"], target["expr"])
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    ]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _traffic(app: Any) -> None:
    """One update, one webhook request and one panel call, so every family has samples."""
    import httpx

    from src.application.common import Remnawave
    from tests.smoke.harness.client import TgUser

    await TgUser(app, app.seed.subscriber_tg, "Subscriber", "subscriber").send("/start")
    transport = httpx.ASGITransport(app=app.fastapi)
    async with httpx.AsyncClient(transport=transport, base_url="http://bot") as client:
        await client.get("/health")
        await client.post("/api/v1/payments/yookassa", content=b"{}")
    # A real HTTP round trip through the instrumented transport (most SDK calls are stubbed).
    await (await app.container.get(Remnawave)).get_user_by_id(app.seed.subscriber_remna_id)


async def _paid_purchase(app: Any) -> None:
    """A Telegram Stars payment by a fresh user, so the income series have a gateway."""
    from tests.smoke.harness.client import TgUser
    from tests.smoke.test_user_flows import _last_invoice, _walk_purchase

    user = TgUser(app, 555_000_901, "Payer", "payer")
    await user.send("/start")
    await _walk_purchase(app, user)
    invoice = _last_invoice(app)
    await user.pre_checkout(invoice.payload, invoice.prices[0].amount)
    await user.successful_payment(invoice.payload, invoice.prices[0].amount)


def _runtime(app: Any, port: int) -> Any:
    from dishka.integrations.aiogram import AiogramMiddlewareData

    from src.core.config.metrics import MetricsConfig
    from src.infrastructure.metrics import MetricsRuntime

    config = app.config.model_copy(
        update={"metrics": MetricsConfig(enabled=True, host="127.0.0.1", port=port)}
    )
    return MetricsRuntime(
        config=config,
        container_factory=lambda: app.container,
        role="app",
        business_context={AiogramMiddlewareData: AiogramMiddlewareData({})},
    )


async def test_dashboard_plots_only_exported_metrics(app: Any) -> None:
    import httpx
    from prometheus_client.parser import text_string_to_metric_families

    await _traffic(app)
    runtime = _runtime(app, port=0)
    await runtime.start()
    try:
        port = runtime._server.port  # noqa: SLF001
        async with httpx.AsyncClient() as client:
            body = (await client.get(f"http://127.0.0.1:{port}/metrics")).text
    finally:
        await runtime.stop()

    families = list(text_string_to_metric_families(body))
    exported = {f.name for f in families} | {s.name for f in families for s in f.samples}

    missing = set()
    for title, expr in _dashboard_exprs():
        for name in METRIC_RE.findall(expr):
            base = next((name[: -len(s)] for s in SUFFIXES if name.endswith(s)), name)
            if name not in exported and base not in exported:
                missing.add((title, name))
    assert not missing, f"dashboard plots metrics the exporter does not have: {sorted(missing)}"


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _vm_query(vm_port: int, expr: str) -> dict[str, Any]:
    url = f"http://127.0.0.1:{vm_port}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - loopback only
        return dict(json.load(response))


@pytest.mark.skipif(not _docker_ready(), reason="Docker is not available")
async def test_dashboard_queries_run_on_victoriametrics(app: Any, tmp_path: Path) -> None:
    exporter_port, vm_port = _free_port(), _free_port()
    (tmp_path / "scrape.yml").write_text(
        "global:\n  scrape_interval: 1s\nscrape_configs:\n  - job_name: remnashop\n"
        f"    static_configs:\n      - targets: ['127.0.0.1:{exporter_port}']\n",
        "utf8",
    )
    tmp_path.chmod(0o755)
    name = f"remnashop-vm-test-{vm_port}"
    started = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "--network", "host",
         "-v", f"{tmp_path}:/cfg:ro", VM_IMAGE,
         f"-httpListenAddr=127.0.0.1:{vm_port}", "-promscrape.config=/cfg/scrape.yml"],
        capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    if started.returncode != 0:
        pytest.skip(f"cannot start VictoriaMetrics: {started.stderr.strip()[:200]}")

    await _paid_purchase(app)
    runtime = _runtime(app, port=exporter_port)
    await runtime.start()
    try:
        # rate() needs two samples in its window: keep traffic going across several scrapes.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            await _traffic(app)
            await asyncio.sleep(1)
            try:
                seen = _vm_query(vm_port, "count(remnashop_http_requests_total)")["data"]["result"]
            except OSError:
                continue
            if seen and time.monotonic() > deadline - 45:
                break
        await asyncio.sleep(6)

        failed, empty = [], []
        for title, expr in _dashboard_exprs():
            result = _vm_query(vm_port, expr.replace("$__rate_interval", "1m"))
            if result.get("status") != "success":
                failed.append((title, expr, result.get("error")))
            elif not result["data"]["result"] and not any(m in expr for m in MAY_BE_EMPTY):
                empty.append((title, expr))
        assert not failed, f"queries rejected by VictoriaMetrics: {failed}"
        assert not empty, f"panels without data after live traffic: {empty}"
    finally:
        await runtime.stop()
        subprocess.run(["docker", "stop", name], capture_output=True, timeout=60)
