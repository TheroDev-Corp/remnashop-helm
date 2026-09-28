"""One object that owns the exporter's lifetime for a process.

The web process starts it from the FastAPI lifespan with business metrics enabled; the taskiq
worker starts it from `WORKER_STARTUP` with business metrics *disabled* — those gauges are
global database numbers, and exporting them from every worker replica would duplicate the same
figure under different pod labels. Runtime, database, Remnawave and taskiq execution metrics
are per-process and therefore exported from both.
"""

from typing import Callable, Optional

from dishka import AsyncContainer
from loguru import logger

from src.core.config import AppConfig

from .business import (
    BusinessCollector,
    BusinessMetricsProvider,
    register_business_collector,
    unregister_business_collector,
)
from .exporter import MetricsServer
from .registry import set_build_info


class MetricsRuntime:
    def __init__(
        self,
        config: AppConfig,
        container_factory: Callable[[], AsyncContainer],
        *,
        role: str,
        collect_business: bool = True,
        business_context: Optional[dict] = None,
    ) -> None:
        self._config = config
        self._container_factory = container_factory
        self._role = role
        self._collect_business = collect_business and config.metrics.business_enabled
        self._business_context = business_context
        self._server: Optional[MetricsServer] = None
        self._collector: Optional[BusinessCollector] = None
        self._provider: Optional[BusinessMetricsProvider] = None

    @property
    def provider(self) -> Optional[BusinessMetricsProvider]:
        return self._provider

    async def start(self) -> None:
        metrics = self._config.metrics
        if not metrics.enabled:
            logger.info("Metrics exporter disabled by configuration")
            return

        set_build_info(
            role=self._role,
            branch=self._config.build.branch,
            commit=self._config.build.commit,
            tag=self._config.build.tag,
        )

        before_scrape = None
        if self._collect_business:
            self._provider = BusinessMetricsProvider(
                container_factory=self._container_factory,
                ttl=metrics.business_cache_ttl,
                timeout=metrics.business_timeout,
                context=self._business_context,
            )
            self._collector = register_business_collector(
                self._provider, panel_overlap=metrics.panel_overlap
            )
            before_scrape = self._provider.ensure_fresh

        self._server = MetricsServer(metrics, before_scrape=before_scrape)
        try:
            await self._server.start()
        except Exception as e:
            # A broken exporter must never stop the bot from serving.
            logger.error(f"Failed to start the metrics exporter: '{e}'")
            self._server = None

    async def stop(self) -> None:
        if self._server is not None:
            await self._server.stop()
            self._server = None
        if self._collector is not None:
            unregister_business_collector(self._collector)
            self._collector = None
        self._provider = None
