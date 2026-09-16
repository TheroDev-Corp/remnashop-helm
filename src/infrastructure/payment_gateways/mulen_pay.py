import hashlib
import uuid
from decimal import Decimal
from hmac import compare_digest
from typing import Any, Final, Union
from uuid import UUID

import orjson
from aiogram import Bot
from fastapi import Request
from httpx import AsyncClient, HTTPStatusError
from loguru import logger

from src.application.dto import PaymentGatewayDto, PaymentResultDto
from src.application.dto.payment_gateway import MulenPayGatewaySettingsDto
from src.core.config import AppConfig
from src.core.enums import TransactionStatus

from .base import BasePaymentGateway


# https://mulenpay.ru/docs/api/
class MulenPayGateway(BasePaymentGateway):
    _client: AsyncClient

    API_BASE: Final[str] = "https://mulenpay.ru/api"

    DEFAULT_PAYMENT_SUBJECT: Final[int] = 4
    DEFAULT_PAYMENT_MODE: Final[int] = 4

    # GET v2/payments/{id} status: 3 = paid, 2 = canceled (string forms accepted as well).
    PAID_STATUSES: Final[frozenset[str]] = frozenset({"3", "success", "paid"})
    CANCELED_STATUSES: Final[frozenset[str]] = frozenset({"2", "cancel", "canceled"})

    def __init__(self, gateway: PaymentGatewayDto, bot: Bot, config: AppConfig) -> None:
        super().__init__(gateway, bot, config)

        if not isinstance(self.data.settings, MulenPayGatewaySettingsDto):
            raise TypeError(
                f"Invalid settings type: expected {MulenPayGatewaySettingsDto.__name__}, "
                f"got {type(self.data.settings).__name__}"
            )

        self._client = self._make_client(
            base_url=self.API_BASE,
            headers={
                "Authorization": f"Bearer {self.data.settings.api_key.get_secret_value()}"  # type: ignore[union-attr]
            },
        )

    async def handle_create_payment(self, amount: Decimal, details: str) -> PaymentResultDto:
        order_uuid = str(uuid.uuid4())
        payload = self._create_payment_payload(amount, details, order_uuid)
        logger.debug(f"Creating payment payload: {payload}")

        try:
            response = await self._client.post("v2/payments", json=payload)
            response.raise_for_status()
            data = orjson.loads(response.content)

            if not data.get("success"):
                raise ValueError(f"MulenPay API error: {data}")

            return self._get_payment_data(data, order_uuid)

        except HTTPStatusError as e:
            logger.error(
                f"HTTP error creating payment. "
                f"Status: '{e.response.status_code}', Body: {e.response.text}"
            )
            raise
        except (KeyError, orjson.JSONDecodeError) as e:
            logger.error(f"Failed to parse response. Error: {e}")
            raise
        except Exception as e:
            logger.exception(f"An unexpected error occurred while creating payment: {e}")
            raise

    async def handle_webhook(self, request: Request) -> Union[tuple[UUID, TransactionStatus], None]:
        logger.debug(f"Received {self.__class__.__name__} webhook request")

        webhook_data = await self._get_webhook_data(request)

        order_uuid = webhook_data.get("uuid")
        if not order_uuid:
            raise ValueError("Required field 'uuid' is missing")

        payment_id = UUID(order_uuid)

        # MulenPay callbacks are unsigned and the order uuid is returned to the payer: always
        # confirm through the API. A `sign` in the body is not trusted — the create-request sign
        # covers only currency/amount/shop, so it cannot authenticate a specific order or status.
        payment_status = await self._fetch_confirmed_status(webhook_data, order_uuid)

        match payment_status:
            case "success":
                transaction_status = TransactionStatus.COMPLETED
            case "cancel":
                transaction_status = TransactionStatus.CANCELED
            case _:
                raise ValueError(f"Unsupported payment_status: {payment_status}")

        return payment_id, transaction_status

    async def _fetch_confirmed_status(self, webhook_data: dict, order_uuid: str) -> str:
        remote_id = webhook_data.get("id")
        if not remote_id:
            logger.warning("Unsigned MulenPay webhook without payment 'id', cannot confirm")
            raise PermissionError("Webhook verification failed")

        try:
            response = await self._client.get(f"v2/payments/{remote_id}")
            response.raise_for_status()
            data = orjson.loads(response.content)
        except Exception as e:
            logger.warning(f"Failed to confirm MulenPay payment '{remote_id}': {e}")
            raise PermissionError("Webhook verification failed") from e

        payment = data.get("payment", data) if isinstance(data, dict) else None
        if not isinstance(payment, dict) or str(payment.get("uuid")) != order_uuid:
            logger.warning(f"MulenPay payment '{remote_id}' does not match order '{order_uuid}'")
            raise PermissionError("Webhook verification failed")

        remote_status = str(payment.get("status")).lower()
        if remote_status in self.PAID_STATUSES:
            return "success"
        if remote_status in self.CANCELED_STATUSES:
            return "cancel"

        logger.warning(f"MulenPay payment '{remote_id}' is not final: status '{remote_status}'")
        raise PermissionError("Webhook verification failed")

    def _create_payment_payload(
        self,
        amount: Decimal,
        details: str,
        order_uuid: str,
    ) -> dict[str, Any]:
        price = str(amount.quantize(Decimal("0.01")))
        return {
            "currency": self.data.currency.value.lower(),
            "amount": price,
            "uuid": order_uuid,
            "shopId": self.data.settings.shop_id,  # type: ignore[union-attr]
            "description": details,
            "sign": self._generate_signature(
                self.data.currency.value.lower(),
                price,
                self.data.settings.shop_id,  # type: ignore[union-attr, arg-type]
            ),
            "items": [
                {
                    "description": details,
                    "quantity": 1,
                    "price": price,
                    "vat_code": self.data.settings.vat_code,  # type: ignore[union-attr]
                    "payment_subject": self.DEFAULT_PAYMENT_SUBJECT,
                    "payment_mode": self.DEFAULT_PAYMENT_MODE,
                }
            ],
        }

    def _generate_signature(self, currency: str, amount: str, shop_id: int) -> str:
        raw = f"{currency}{amount}{shop_id}{self.data.settings.secret_key.get_secret_value()}"  # type: ignore[union-attr]
        return hashlib.sha1(raw.encode()).hexdigest()

    def _get_payment_data(self, data: dict[str, Any], order_uuid: str) -> PaymentResultDto:
        payment_url = data.get("paymentUrl")
        if not payment_url:
            raise KeyError("Invalid response from MulenPay API: missing 'paymentUrl'")

        return PaymentResultDto(id=UUID(order_uuid), url=str(payment_url))

    def _verify_webhook(self, data: dict) -> bool:
        sign = data.get("sign")
        if not sign:
            logger.warning("Webhook is missing 'sign' field")
            return False

        shop_id = self.data.settings.shop_id  # type: ignore[union-attr]
        raw_amount = data.get("amount", "")
        currency = data.get("currency", "rub").lower()

        try:
            amount = f"{Decimal(str(raw_amount)):.2f}"
        except Exception:
            logger.warning(f"Failed to parse webhook amount: {raw_amount!r}")
            return False

        expected = self._generate_signature(currency, amount, shop_id)  # type: ignore[arg-type]

        if not compare_digest(expected, sign):
            logger.warning("Invalid MulenPay webhook signature")
            return False

        return True
