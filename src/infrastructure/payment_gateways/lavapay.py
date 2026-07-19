import hashlib
import hmac
import json
import uuid
from decimal import Decimal
from typing import Any, Final, Union
from uuid import UUID

import orjson
from aiogram import Bot
from fastapi import Request
from httpx import AsyncClient, HTTPStatusError
from loguru import logger

from src.application.dto import PaymentGatewayDto, PaymentResultDto
from src.application.dto.payment_gateway import LavaPayGatewaySettingsDto
from src.core.config import AppConfig
from src.core.enums import TransactionStatus

from .base import BasePaymentGateway


class LavaPayGateway(BasePaymentGateway):
    _client: AsyncClient

    API_BASE: Final[str] = "https://api.lava.ru/business/"

    def __init__(self, gateway: PaymentGatewayDto, bot: Bot, config: AppConfig) -> None:
        super().__init__(gateway, bot, config)

        if not isinstance(self.data.settings, LavaPayGatewaySettingsDto):
            raise TypeError(
                f"Invalid settings type: expected {LavaPayGatewaySettingsDto.__name__}, "
                f"got {type(self.data.settings).__name__}"
            )

        self._client = self._make_client(base_url=self.API_BASE)

    async def handle_create_payment(self, amount: Decimal, details: str) -> PaymentResultDto:
        order_id = str(uuid.uuid4())
        payload = {
            "shopId": self.data.settings.shop_id,  # type: ignore[union-attr]
            "sum": float(amount),
            "orderId": order_id,
            "comment": details[:255],
            "successUrl": await self._get_bot_redirect_url(),
            "failUrl": await self._get_bot_redirect_url(),
            "hookUrl": self.config.get_webhook(self.data.type),
        }

        # Sign payload using HMAC-SHA256 with secret_key (api_key)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        api_key = self.data.settings.api_key.get_secret_value()  # type: ignore[union-attr]
        signature = hmac.new(
            api_key.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Signature": signature,
        }
        logger.debug(f"Creating LavaPay payment payload: {payload}")

        try:
            response = await self._client.post(
                "invoice/create", content=body.encode("utf-8"), headers=headers
            )
            response.raise_for_status()
            data = orjson.loads(response.content)

            if data.get("error"):
                raise ValueError(f"LavaPay API error: {data.get('error')}")

            invoice_data = data.get("data")
            if not invoice_data:
                raise ValueError(f"LavaPay response missing 'data': {data}")

            return self._get_payment_data(invoice_data)

        except HTTPStatusError as e:
            logger.error(
                f"HTTP error creating LavaPay payment. "
                f"Status: '{e.response.status_code}', Body: {e.response.text}"
            )
            raise
        except (KeyError, orjson.JSONDecodeError) as e:
            logger.error(f"Failed to parse LavaPay response. Error: {e}")
            raise
        except Exception as e:
            logger.exception(f"Unexpected error creating LavaPay payment: {e}")
            raise

    async def handle_webhook(self, request: Request) -> Union[tuple[UUID, TransactionStatus], None]:
        logger.debug("Received LavaPayGateway webhook request")

        raw_body = await request.body()
        webhook_data = orjson.loads(raw_body)
        logger.debug(f"LavaPay webhook data: {webhook_data}")

        if not self._verify_webhook(request, webhook_data):
            raise PermissionError("LavaPay webhook signature verification failed")

        if webhook_data.get("type") != 1:
            logger.debug(f"Ignoring non-invoice webhook of type: {webhook_data.get('type')}")
            return None

        payment_id_str = webhook_data.get("order_id")
        if not payment_id_str:
            raise ValueError("Required field 'order_id' is missing in webhook data")

        status = webhook_data.get("status")
        payment_id = UUID(payment_id_str)

        match status:
            case "success":
                transaction_status = TransactionStatus.COMPLETED
            case "fail" | "cancel" | "expired":
                transaction_status = TransactionStatus.CANCELED
            case _:
                logger.debug(f"Received unexpected status: {status}")
                return None

        return payment_id, transaction_status

    def _get_payment_data(self, data: dict[str, Any]) -> PaymentResultDto:
        invoice_id = data.get("id")
        if not invoice_id:
            raise KeyError("Invalid LavaPay response: missing 'id'")

        payment_url = data.get("url")
        if not payment_url:
            raise KeyError("Invalid LavaPay response: missing 'url'")

        return PaymentResultDto(id=UUID(invoice_id), url=str(payment_url))

    def _verify_webhook(self, request: Request, data: dict) -> bool:
        sign = data.get("sign")
        if not sign:
            logger.warning("LavaPay webhook is missing 'sign' field")
            return False

        invoice_id = data.get("invoice_id")
        amount = data.get("amount")
        pay_time = data.get("pay_time")

        if not all([invoice_id, amount, pay_time]):
            logger.warning(
                f"LavaPay webhook missing required signature fields: "
                f"invoice_id={invoice_id}, amount={amount}, pay_time={pay_time}"
            )
            return False

        secret_key_2 = self.data.settings.secret_key_2.get_secret_value()  # type: ignore[union-attr]
        raw = f"{invoice_id}:{amount}:{pay_time}:{secret_key_2}"
        expected = hashlib.md5(raw.encode("utf-8")).hexdigest()

        if not hmac.compare_digest(expected.lower(), sign.lower()):
            logger.warning("Invalid LavaPay webhook signature")
            return False

        return True
