"""The single Prometheus registry of the process and every static metric in it.

A dedicated `CollectorRegistry` (instead of `prometheus_client.REGISTRY`) keeps the exported
series under this package's control and makes the exporter safe to build repeatedly in tests.
Label sets are deliberately low-cardinality: no telegram_id, payment_id or user_remna_id ever
reaches a label — route/endpoint templates and enum-like values only.
"""

from typing import Final, Iterator, Optional

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
)
from prometheus_client.gc_collector import GCCollector
from prometheus_client.metrics_core import GaugeMetricFamily, InfoMetricFamily, Metric
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector

from src.__version__ import __version__
from src.core.utils.time import get_uptime

NAMESPACE: Final[str] = "remnashop"

REGISTRY: Final[CollectorRegistry] = CollectorRegistry(auto_describe=True)

# ----------------------------------------------------------------- runtime / process
ProcessCollector(registry=REGISTRY)
PlatformCollector(registry=REGISTRY)
GCCollector(registry=REGISTRY)

BUILD_INFO: Final[Info] = Info(
    "remnashop_build",
    "Remnashop build information",
    registry=REGISTRY,
)
BUILD_INFO.info({"version": __version__, "branch": "", "commit": "", "tag": "", "role": ""})

UPTIME: Final[Gauge] = Gauge(
    "remnashop_uptime_seconds",
    "Seconds since the process imported src.core.utils.time",
    registry=REGISTRY,
)
UPTIME.set_function(get_uptime)

# ----------------------------------------------------------------- HTTP (FastAPI)
HTTP_REQUESTS: Final[Counter] = Counter(
    "remnashop_http_requests",
    "HTTP requests served by the FastAPI application",
    labelnames=("method", "path", "status"),
    registry=REGISTRY,
)
HTTP_REQUEST_DURATION: Final[Histogram] = Histogram(
    "remnashop_http_request_duration_seconds",
    "HTTP request duration by route template",
    labelnames=("method", "path"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)
HTTP_REQUESTS_IN_PROGRESS: Final[Gauge] = Gauge(
    "remnashop_http_requests_in_progress",
    "HTTP requests currently being served",
    labelnames=("method", "path"),
    registry=REGISTRY,
)
HTTP_EXCEPTIONS: Final[Counter] = Counter(
    "remnashop_http_unhandled_exceptions",
    "Exceptions that escaped the FastAPI application",
    labelnames=("method", "path", "exception"),
    registry=REGISTRY,
)

# ----------------------------------------------------------------- database (SQLAlchemy)
DB_POOL_WAIT: Final[Histogram] = Histogram(
    "remnashop_db_pool_wait_seconds",
    "Time spent acquiring a connection from the SQLAlchemy pool",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)
DB_CONNECTION_USAGE: Final[Histogram] = Histogram(
    "remnashop_db_connection_usage_seconds",
    "Time a pooled connection stays checked out",
    buckets=(0.005, 0.025, 0.1, 0.5, 1.0, 5.0, 15.0, 60.0, 300.0),
    registry=REGISTRY,
)
DB_QUERY_DURATION: Final[Histogram] = Histogram(
    "remnashop_db_query_duration_seconds",
    "SQL statement execution time",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0),
    registry=REGISTRY,
)
DB_POOL_EVENTS: Final[Counter] = Counter(
    "remnashop_db_pool_events",
    "SQLAlchemy pool lifecycle events",
    labelnames=("event",),
    registry=REGISTRY,
)
DB_ERRORS: Final[Counter] = Counter(
    "remnashop_db_errors",
    "Database errors by exception class (pool timeouts included)",
    labelnames=("exception",),
    registry=REGISTRY,
)

# ----------------------------------------------------------------- taskiq
TASKIQ_TASKS_SENT: Final[Counter] = Counter(
    "remnashop_taskiq_tasks_sent",
    "Tasks enqueued to the broker (counted in the sending process)",
    labelnames=("task",),
    registry=REGISTRY,
)
TASKIQ_TASKS_EXECUTED: Final[Counter] = Counter(
    "remnashop_taskiq_tasks_executed",
    "Tasks executed by the worker",
    labelnames=("task", "result"),
    registry=REGISTRY,
)
TASKIQ_TASK_ERRORS: Final[Counter] = Counter(
    "remnashop_taskiq_task_errors",
    "Task failures by exception class",
    labelnames=("task", "exception"),
    registry=REGISTRY,
)
TASKIQ_TASK_DURATION: Final[Histogram] = Histogram(
    "remnashop_taskiq_task_duration_seconds",
    "Task execution time",
    labelnames=("task",),
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 15.0, 60.0, 300.0, 900.0),
    registry=REGISTRY,
)

# ----------------------------------------------------------------- aiogram
TELEGRAM_UPDATES: Final[Counter] = Counter(
    "remnashop_telegram_updates",
    "Telegram updates fed to the dispatcher",
    labelnames=("type",),
    registry=REGISTRY,
)
TELEGRAM_UPDATE_DURATION: Final[Histogram] = Histogram(
    "remnashop_telegram_update_duration_seconds",
    "Telegram update processing time",
    labelnames=("type",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)
TELEGRAM_UPDATE_EXCEPTIONS: Final[Counter] = Counter(
    "remnashop_telegram_update_exceptions",
    "Updates whose exception was not handled by the error middleware",
    labelnames=("type", "exception"),
    registry=REGISTRY,
)

# ----------------------------------------------------------------- Remnawave panel
REMNAWAVE_REQUESTS: Final[Counter] = Counter(
    "remnashop_remnawave_requests",
    "Remnawave panel API calls by endpoint template and response code",
    labelnames=("method", "endpoint", "status"),
    registry=REGISTRY,
)
REMNAWAVE_REQUEST_DURATION: Final[Histogram] = Histogram(
    "remnashop_remnawave_request_duration_seconds",
    "Remnawave panel API call duration",
    labelnames=("method", "endpoint"),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)
REMNAWAVE_ERRORS: Final[Counter] = Counter(
    "remnashop_remnawave_errors",
    "Remnawave panel API calls that never produced a response",
    labelnames=("endpoint", "exception"),
    registry=REGISTRY,
)


class _PanelStateCollector:
    """Panel availability/version observed from traffic the bot already makes.

    Nothing is emitted until the panel has actually been talked to, so a freshly started
    process never reports a panel it has not contacted as being down.
    """

    def __init__(self) -> None:
        self.available: Optional[bool] = None
        self.version: Optional[str] = None

    def describe(self) -> list[Metric]:
        return []

    def collect(self) -> Iterator[Metric]:
        if self.available is not None:
            yield GaugeMetricFamily(
                "remnashop_remnawave_up",
                "Last observed Remnawave panel availability (1 answered, 0 unavailable); "
                "updated from traffic the bot already makes, never by scraping the panel",
                value=1.0 if self.available else 0.0,
            )
        if self.version:
            info = InfoMetricFamily(
                "remnashop_remnawave_panel",
                "Remnawave panel version reported at the last successful connection check",
            )
            info.add_metric([], {"version": self.version})
            yield info


PANEL_STATE: Final[_PanelStateCollector] = _PanelStateCollector()
REGISTRY.register(PANEL_STATE)  # type: ignore[arg-type]

# ----------------------------------------------------------------- payment webhooks
PAYMENT_WEBHOOKS: Final[Counter] = Counter(
    "remnashop_payment_webhooks",
    "Payment gateway webhooks by gateway and outcome",
    labelnames=("gateway", "outcome"),
    registry=REGISTRY,
)

# ----------------------------------------------------------------- business collection health
BUSINESS_COLLECTION_ERRORS: Final[Counter] = Counter(
    "remnashop_business_collection_errors",
    "Failed attempts to refresh the cached business metrics snapshot",
    registry=REGISTRY,
)
BUSINESS_COLLECTIONS: Final[Counter] = Counter(
    "remnashop_business_collections",
    "Business metrics snapshot refreshes that completed successfully",
    registry=REGISTRY,
)
BUSINESS_COLLECTION_DURATION: Final[Gauge] = Gauge(
    "remnashop_business_collection_duration_seconds",
    "Duration of the last successful business metrics snapshot refresh",
    registry=REGISTRY,
)
BUSINESS_LAST_SUCCESS: Final[Gauge] = Gauge(
    "remnashop_business_collection_last_success_timestamp_seconds",
    "Unix timestamp of the last successful business metrics snapshot refresh",
    registry=REGISTRY,
)


def set_build_info(
    *,
    role: str,
    branch: Optional[str] = None,
    commit: Optional[str] = None,
    tag: Optional[str] = None,
) -> None:
    BUILD_INFO.info(
        {
            "version": __version__,
            "branch": branch or "",
            "commit": commit or "",
            "tag": tag or "",
            "role": role,
        }
    )


def set_panel_version(version: str) -> None:
    PANEL_STATE.version = version
    PANEL_STATE.available = True


def set_panel_available(available: bool) -> None:
    PANEL_STATE.available = available
