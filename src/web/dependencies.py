import secrets
from typing import Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from src.core.config import AppConfig

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@inject
async def require_api_key(
    config: FromDishka[AppConfig],
    x_api_key: Optional[str] = Security(api_key_header),
) -> None:
    if config.api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="API key not configured"
        )
    expected = config.api_key.get_secret_value()
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
