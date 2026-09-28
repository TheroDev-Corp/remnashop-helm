from pydantic import Field

from .base import BaseConfig


class MetricsConfig(BaseConfig, env_prefix="METRICS_"):
    """Prometheus exporter settings.

    The exporter listens on its own port so the scrape endpoint is never reachable through the
    ingress that publishes the Telegram/Remnawave/payment webhooks. It carries no
    authentication on purpose: the port is only exposed inside the cluster.
    """

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 9100
    path: str = "/metrics"

    # Business gauges are read through the statistics interactors and cached, so a 15s scrape
    # interval cannot turn into 15s-spaced heavy aggregate queries.
    business_enabled: bool = True
    business_cache_ttl: float = Field(default=45.0, ge=1.0)
    # Hard cap for one business collection pass; on timeout the previous snapshot is served.
    business_timeout: float = Field(default=25.0, ge=1.0)

    @property
    def normalized_path(self) -> str:
        path = self.path.strip()
        if not path.startswith("/"):
            path = f"/{path}"
        return path.rstrip("/") or "/metrics"
