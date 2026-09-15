"""Payment webhooks must never complete a transaction on the notification body alone."""

from decimal import Decimal
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import orjson
import pytest
from starlette.datastructures import Headers

from src.application.use_cases.gateways.commands.payment import ProcessPayment, ProcessPaymentDto
from src.core.enums import PaymentGatewayType, TransactionStatus
from src.infrastructure.payment_gateways.mulen_pay import MulenPayGateway
from src.infrastructure.payment_gateways.pay_master import PayMasterGateway
from src.infrastructure.payment_gateways.yookassa import YookassaGateway
from src.infrastructure.payment_gateways.yoomoney import YoomoneyGateway
from src.telegram.routers.extra.payment import on_pre_checkout


def _request(body: bytes, headers: Optional[dict[str, str]] = None) -> MagicMock:
    request = MagicMock()
    request.body = AsyncMock(return_value=body)
    request.headers = Headers(headers or {})
    return request


def _gateway(cls: type, api_response: Optional[Any] = None, status_code: int = 200) -> Any:
    gateway = object.__new__(cls)
    gateway.data = MagicMock()
    gateway._client = MagicMock()
    gateway._client.get = AsyncMock(
        return_value=httpx.Response(
            status_code,
            content=orjson.dumps(api_response),
            request=httpx.Request("GET", "https://gateway.example.com"),
        )
    )
    return gateway


# --------------------------------------------------------------------------- MulenPay


@pytest.mark.asyncio
async def test_mulenpay_unsigned_body_without_payment_id_is_rejected():
    gateway = _gateway(MulenPayGateway)
    order = str(uuid4())

    with pytest.raises(PermissionError):
        await gateway.handle_webhook(
            _request(orjson.dumps({"uuid": order, "payment_status": "success"}))
        )

    gateway._client.get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remote_status", "expected"),
    [(3, TransactionStatus.COMPLETED), (2, TransactionStatus.CANCELED)],
)
async def test_mulenpay_status_comes_from_api(remote_status, expected):
    order = str(uuid4())
    gateway = _gateway(
        MulenPayGateway, {"success": True, "payment": {"uuid": order, "status": remote_status}}
    )

    result = await gateway.handle_webhook(
        _request(orjson.dumps({"id": 42, "uuid": order, "payment_status": "success"}))
    )

    assert result == (UUID(order), expected)
    gateway._client.get.assert_awaited_once_with("v2/payments/42")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payment",
    [{"uuid": "00000000-0000-0000-0000-000000000000", "status": 3}, {"status": 1}],
)
async def test_mulenpay_refuses_unpaid_or_foreign_payment(payment):
    order = str(uuid4())
    payment.setdefault("uuid", order)
    gateway = _gateway(MulenPayGateway, {"success": True, "payment": payment})

    with pytest.raises(PermissionError):
        await gateway.handle_webhook(
            _request(orjson.dumps({"id": 42, "uuid": order, "payment_status": "success"}))
        )


@pytest.mark.asyncio
async def test_mulenpay_body_sign_is_not_trusted():
    order = str(uuid4())
    gateway = _gateway(MulenPayGateway, {"success": True, "payment": {"uuid": order, "status": 1}})
    gateway._verify_webhook = MagicMock(return_value=True)

    # Even a "valid" sign in the body must not skip the API confirmation.
    with pytest.raises(PermissionError):
        await gateway.handle_webhook(
            _request(
                orjson.dumps({"id": 42, "uuid": order, "payment_status": "success", "sign": "x"})
            )
        )

    gateway._verify_webhook.assert_not_called()
    gateway._client.get.assert_awaited_once_with("v2/payments/42")


# --------------------------------------------------------------------------- PayMaster


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remote_status", "expected"),
    [
        ("Settled", TransactionStatus.COMPLETED),
        ("Cancelled", TransactionStatus.CANCELED),
        ("Authorized", None),
        ("Pending", None),
    ],
)
async def test_paymaster_status_comes_from_api(remote_status, expected):
    payment_id = str(uuid4())
    gateway = _gateway(PayMasterGateway, {"id": payment_id, "status": remote_status})

    # The body always claims Settled: only the API answer counts.
    result = await gateway.handle_webhook(
        _request(orjson.dumps({"id": payment_id, "status": "Settled"}))
    )

    assert result == ((UUID(payment_id), expected) if expected else None)
    gateway._client.get.assert_awaited_once_with(f"payments/{payment_id}")


@pytest.mark.asyncio
async def test_paymaster_api_failure_rejects_webhook():
    payment_id = str(uuid4())
    gateway = _gateway(PayMasterGateway, {"error": "not found"}, status_code=404)

    with pytest.raises(PermissionError):
        await gateway.handle_webhook(
            _request(orjson.dumps({"id": payment_id, "status": "Settled"}))
        )


# --------------------------------------------------------------------------- YooKassa

TRUSTED_IP = {"CF-Connecting-IP": "185.71.76.1"}


@pytest.mark.asyncio
async def test_yookassa_spoofed_ip_cannot_complete_unpaid_payment():
    payment_id = str(uuid4())
    gateway = _gateway(YookassaGateway, {"id": payment_id, "status": "canceled"})
    body = {"event": "payment.succeeded", "object": {"id": payment_id, "status": "succeeded"}}

    result = await gateway.handle_webhook(_request(orjson.dumps(body), TRUSTED_IP))

    assert result == (UUID(payment_id), TransactionStatus.CANCELED)
    gateway._client.get.assert_awaited_once_with(f"v3/payments/{payment_id}")


@pytest.mark.asyncio
async def test_yookassa_completes_only_confirmed_payment():
    payment_id = str(uuid4())
    gateway = _gateway(YookassaGateway, {"id": payment_id, "status": "succeeded"})
    body = {"event": "payment.succeeded", "object": {"id": payment_id, "status": "succeeded"}}

    result = await gateway.handle_webhook(_request(orjson.dumps(body), TRUSTED_IP))

    assert result == (UUID(payment_id), TransactionStatus.COMPLETED)


@pytest.mark.asyncio
async def test_yookassa_ignores_refund_events():
    gateway = _gateway(YookassaGateway)
    body = {"event": "refund.succeeded", "object": {"id": str(uuid4()), "status": "succeeded"}}

    result = await gateway.handle_webhook(_request(orjson.dumps(body), TRUSTED_IP))

    assert result is None
    gateway._client.get.assert_not_awaited()


# --------------------------------------------------------------------------- YooMoney


def _yoomoney_body(**fields: str) -> bytes:
    data = {
        "notification_type": "card-incoming",
        "operation_id": "1",
        "label": str(uuid4()),
        "currency": "643",
        "withdraw_amount": "1000.00",
        "unaccepted": "false",
        "codepro": "false",
        "sign": "valid",
        **fields,
    }
    return "&".join(f"{k}={v}" for k, v in data.items()).encode()


def _yoomoney() -> YoomoneyGateway:
    gateway = _gateway(YoomoneyGateway)
    gateway._verify_webhook = MagicMock(return_value=True)
    return gateway


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields", [{"unaccepted": "true"}, {"codepro": "true"}, {"currency": "840"}]
)
async def test_yoomoney_rejects_uncredited_transfers(fields):
    with pytest.raises(PermissionError):
        await _yoomoney().handle_webhook(_request(_yoomoney_body(**fields)))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("withdraw_amount", "expected"),
    [("2.00", False), ("999.99", False), ("1000.00", True), ("garbage", False)],
)
async def test_yoomoney_paid_amount_must_cover_price(withdraw_amount, expected):
    transaction = MagicMock(payment_id=uuid4())
    transaction.pricing.final_amount = Decimal("1000")
    request = _request(_yoomoney_body(withdraw_amount=withdraw_amount))

    assert await _yoomoney().verify_paid_amount(request, transaction) is expected


# --------------------------------------------------------------------------- ProcessPayment


@pytest.mark.asyncio
async def test_late_confirmation_completes_auto_canceled_transaction(sample_user_dto):
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.commit = AsyncMock()
    transaction = MagicMock(gateway_type=PaymentGatewayType.YOOKASSA, user_id=sample_user_dto.id)
    transaction_dao = MagicMock()
    transaction_dao.get_by_payment_id = AsyncMock(return_value=transaction)
    transaction_dao.transition_status = AsyncMock(return_value=transaction)
    user_dao = MagicMock()
    user_dao.get_by_id = AsyncMock(return_value=sample_user_dto)
    use_case = ProcessPayment(uow, user_dao, transaction_dao, *(MagicMock() for _ in range(7)))
    use_case._handle_success = AsyncMock()
    payment_id = uuid4()

    await use_case.system(
        ProcessPaymentDto(payment_id, TransactionStatus.COMPLETED, PaymentGatewayType.YOOKASSA)
    )

    _, new_status, allowed = transaction_dao.transition_status.await_args.args
    assert new_status == TransactionStatus.COMPLETED
    assert TransactionStatus.CANCELED in allowed
    use_case._handle_success.assert_awaited_once()


# --------------------------------------------------------------------------- Telegram Stars


def _pre_checkout(payload: str) -> MagicMock:
    query = MagicMock()
    query.invoice_payload = payload
    query.answer = AsyncMock()
    return query


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "ok"),
    [
        ({}, True),
        ({"status": TransactionStatus.CANCELED}, True),
        ({"status": TransactionStatus.COMPLETED}, False),
        ({"user_id": 2}, False),
        ({"gateway_type": PaymentGatewayType.YOOKASSA}, False),
    ],
)
async def test_pre_checkout_validates_transaction(sample_user_dto, overrides, ok):
    fields = {
        "status": TransactionStatus.PENDING,
        "user_id": sample_user_dto.id,
        "gateway_type": PaymentGatewayType.TELEGRAM_STARS,
        **overrides,
    }
    transaction_dao = MagicMock()
    transaction_dao.get_by_payment_id = AsyncMock(return_value=MagicMock(**fields))
    query = _pre_checkout(str(uuid4()))

    await on_pre_checkout(query, sample_user_dto, transaction_dao)

    assert query.answer.await_args.kwargs["ok"] is ok


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["", "not-a-uuid"])
async def test_pre_checkout_rejects_invalid_payload(sample_user_dto, payload):
    transaction_dao = MagicMock()
    transaction_dao.get_by_payment_id = AsyncMock()
    query = _pre_checkout(payload)

    await on_pre_checkout(query, sample_user_dto, transaction_dao)

    assert query.answer.await_args.kwargs["ok"] is False
    transaction_dao.get_by_payment_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_pre_checkout_rejects_unknown_transaction(sample_user_dto):
    transaction_dao = MagicMock()
    transaction_dao.get_by_payment_id = AsyncMock(return_value=None)
    query = _pre_checkout(str(uuid4()))

    await on_pre_checkout(query, sample_user_dto, transaction_dao)

    assert query.answer.await_args.kwargs["ok"] is False
