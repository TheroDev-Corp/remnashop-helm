import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Annotated, Optional, cast

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import Depends, Header, HTTPException, status

from src.application.common.dao import UserDao
from src.application.dto import UserDto
from src.core.config import AppConfig


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _normalize_decimal_str(value: Decimal) -> str:
    if value == value.to_integral():
        return str(int(value))

    normalized = value.quantize(Decimal("0.01")).normalize()
    return format(normalized, "f")


def _decode_access_token(token: str, key: str) -> dict[str, int]:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token format",
        ) from e

    expected_signature = hmac.new(
        key.encode("utf-8"),
        f"{header_b64}.{payload_b64}".encode("utf-8"),
        hashlib.sha256,
    ).digest()

    if not hmac.compare_digest(expected_signature, _b64url_decode(signature_b64)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token signature",
        )

    payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    return cast(dict[str, int], payload)


async def _get_user_from_auth_header(
    authorization: Annotated[Optional[str], Header(alias="Authorization")],
    user_dao: UserDao,
    config: AppConfig,
) -> UserDto:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must use Bearer token",
        )

    token = authorization.removeprefix("Bearer ").strip()
    payload = _decode_access_token(token, config.crypt_key.get_secret_value())
    user_id = int(payload["sub"])
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


@inject
async def _get_current_user(
    authorization: Annotated[Optional[str], Header(alias="Authorization")] = None,
    user_dao: FromDishka[UserDao] = None,  # type: ignore[assignment]
    config: FromDishka[AppConfig] = None,  # type: ignore[assignment]
) -> UserDto:
    return await _get_user_from_auth_header(authorization, user_dao, config)


CurrentUser = Annotated[UserDto, Depends(_get_current_user)]
