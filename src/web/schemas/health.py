from typing import Optional

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class PoolMetricsSchema(BaseModel):
    size: int
    checked_in: int
    checked_out: int
    overflow: int
    total_connections: int
    utilization: str


class DatabaseStatusSchema(BaseModel):
    ok: bool
    latency_ms: Optional[float]
    pool: Optional[PoolMetricsSchema]


class HealthChecks(BaseModel):
    database: DatabaseStatusSchema
    redis: bool


class HealthDetailsResponse(BaseModel):
    status: str
    version: Optional[str]
    branch: Optional[str]
    commit: Optional[str]
    checks: HealthChecks
