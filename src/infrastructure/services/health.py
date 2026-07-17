import time
from dataclasses import dataclass
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass
class PoolMetrics:
    size: int
    checked_in: int
    checked_out: int
    overflow: int
    total_connections: int
    utilization: str


@dataclass
class DatabaseStatus:
    ok: bool
    latency_ms: Optional[float]
    pool: Optional[PoolMetrics]


@dataclass
class HealthStatus:
    database: DatabaseStatus
    redis: bool

    @property
    def ok(self) -> bool:
        return self.database.ok and self.redis


class HealthService:
    def __init__(self, engine: AsyncEngine, redis: Redis) -> None:
        self.engine = engine
        self.redis = redis

    async def check(self) -> HealthStatus:
        database = await self._check_database()
        redis = await self._check_redis()
        return HealthStatus(database=database, redis=redis)

    async def _check_database(self) -> DatabaseStatus:
        latency_ms: Optional[float] = None
        try:
            async with self.engine.connect() as conn:
                start = time.time()
                await conn.execute(text("SELECT 1"))
                latency_ms = round((time.time() - start) * 1000, 2)
            pool = self._collect_pool_metrics()
            return DatabaseStatus(ok=True, latency_ms=latency_ms, pool=pool)
        except Exception:
            return DatabaseStatus(ok=False, latency_ms=None, pool=None)

    def _collect_pool_metrics(self) -> Optional[PoolMetrics]:
        pool = self.engine.pool
        size = getattr(pool, "size", lambda: 0)()
        checked_in = getattr(pool, "checkedin", lambda: 0)()
        checked_out = getattr(pool, "checkedout", lambda: 0)()
        overflow = max(0, checked_out - size)
        total = checked_in + checked_out
        utilization = (checked_out / size * 100) if size > 0 else 0.0
        return PoolMetrics(
            size=size,
            checked_in=checked_in,
            checked_out=checked_out,
            overflow=overflow,
            total_connections=total,
            utilization=f"{utilization:.1f}%",
        )

    async def _check_redis(self) -> bool:
        try:
            await self.redis.ping()  # type: ignore[misc]
            return True
        except Exception:
            return False
