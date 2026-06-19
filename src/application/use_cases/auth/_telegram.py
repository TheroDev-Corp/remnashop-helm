import hashlib
import hmac
import time
from typing import Any

from src.core.constants import TELEGRAM_AUTH_MAX_AGE_SECONDS


def verify_telegram_auth(data: dict[str, Any], bot_token: str) -> bool:
    telegram_hash = str(data.get("hash", ""))
    auth_date = int(data.get("auth_date", 0))

    if int(time.time()) - auth_date > TELEGRAM_AUTH_MAX_AGE_SECONDS:
        return False

    data_check = {k: str(v) for k, v in data.items() if k != "hash"}
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data_check.items()))
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    expected = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, telegram_hash)
