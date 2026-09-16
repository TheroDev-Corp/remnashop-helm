"""A payment the gateway confirmed must never be lost, a trial must never be granted twice, and
YooKassa forwarded-IP headers are honored only from trusted proxies."""

import asyncio
from datetime import timedelta
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql
from starlette.datastructures import Headers

from src.application.use_cases.gateways.commands.payment import (
    FULFILLMENT_GRACE,
    MAX_FULFILLMENT_ATTEMPTS,
    ProcessPayment,
    ProcessPaymentDto,
    RetryUnfulfilledPayments,
)
from src.application.use_cases.subscription.commands.purchase import (
    ActivateTrialSubscription,
    ActivateTrialSubscriptionDto,
)
from src.core.enums import PaymentGatewayType, PurchaseType, TransactionStatus
from src.core.exceptions import TrialNotAvailableError
from src.core.utils.time import datetime_now
from src.infrastructure.database.dao.transaction import TransactionDaoImpl
from src.infrastructure.payment_gateways.yookassa import YookassaGateway

GATEWAY = PaymentGatewayType.YOOKASSA


class FakeTransactionDao:
    """In-memory model of the conditional updates in TransactionDaoImpl."""

    def __init__(self, transaction: Any, status: TransactionStatus = TransactionStatus.PENDING):
        self.transaction = transaction
        self.status = status
        self.attempts = 0
        self.next_retry_at = None
        self.fulfilled_at = None
        self.error: Optional[str] = None
        self.updated_at = datetime_now()

    async def get_by_payment_id(self, payment_id: UUID) -> Any:
        return self.transaction if payment_id == self.transaction.payment_id else None

    async def transition_status(self, payment_id, new_status, allowed_current) -> Any:
        if self.status not in tuple(allowed_current):
            return None
        self.status = new_status
        self.updated_at = datetime_now()
        return self.transaction

    async def claim_fulfillment(self, payment_id, max_attempts, lease) -> Optional[int]:
        now = datetime_now()
        if (
            self.status != TransactionStatus.COMPLETED
            or self.fulfilled_at is not None
            or self.attempts >= max_attempts
            or (self.next_retry_at is not None and self.next_retry_at > now)
        ):
            return None
        self.attempts += 1
        self.next_retry_at = now + lease
        return self.attempts

    async def mark_fulfilled(self, payment_id) -> bool:
        if self.fulfilled_at is not None:
            return False
        self.fulfilled_at = datetime_now()
        self.next_retry_at = None
        return True

    async def mark_fulfillment_failed(self, payment_id, retry_at, error) -> bool:
        first = self.error is None
        self.error = error
        self.next_retry_at = retry_at
        return first

    async def get_unfulfilled_payment_ids(self, max_attempts, grace, limit=50) -> list[UUID]:
        now = datetime_now()
        due = (self.next_retry_at is None and self.updated_at < now - grace) or (
            self.next_retry_at is not None and self.next_retry_at <= now
        )
        if (
            self.status == TransactionStatus.COMPLETED
            and self.fulfilled_at is None
            and self.attempts < max_attempts
            and due
        ):
            return [self.transaction.payment_id]
        return []

    def expire_backoff(self) -> None:
        self.next_retry_at = datetime_now() - timedelta(seconds=1)
        self.updated_at = datetime_now() - FULFILLMENT_GRACE - timedelta(seconds=1)


@pytest.fixture
def uow():
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.commit = AsyncMock()
    return uow


@pytest.fixture
def transaction(sample_user_dto, sample_plan_dto):
    pricing = MagicMock(is_free=False, final_amount=100, original_amount=100, discount_percent=0)
    return MagicMock(
        payment_id=uuid4(),
        user_id=sample_user_dto.id,
        is_test=False,
        gateway_type=GATEWAY,
        purchase_type=PurchaseType.RENEW,
        plan_snapshot=sample_plan_dto,
        pricing=pricing,
        currency=MagicMock(symbol="₽"),
    )


class Env:
    def __init__(self, uow, sample_user_dto, sample_subscription_dto, dao):
        self.dao = dao
        self.granted: list[UUID] = []
        self.purchase_error: Optional[Exception] = None

        self.user_dao = MagicMock()
        self.user_dao.get_by_id = AsyncMock(return_value=sample_user_dto)
        self.subscription_dao = MagicMock()
        self.subscription_dao.get_current = AsyncMock(return_value=sample_subscription_dto)
        self.event_publisher = MagicMock(publish=AsyncMock())
        self.notifier = MagicMock(
            notify_system=AsyncMock(), notify_user=AsyncMock(), notify_admins=AsyncMock()
        )
        self.redirect = MagicMock(to_success_payment=AsyncMock(), to_failed_payment=AsyncMock())
        self.referrals = MagicMock()
        self.referrals.system = AsyncMock()
        self.purchase = MagicMock()
        self.purchase.system = AsyncMock(side_effect=self._purchase)

        self.process_payment = ProcessPayment(
            uow,
            self.user_dao,
            dao,
            self.subscription_dao,
            MagicMock(),
            self.event_publisher,
            self.notifier,
            self.redirect,
            self.referrals,
            self.purchase,
        )
        self.retry = RetryUnfulfilledPayments(uow, dao, self.process_payment)

    async def _purchase(self, dto) -> None:
        # Mirrors PurchaseSubscription: panel + DB work, then before_commit, then commit.
        await asyncio.sleep(0)
        if self.purchase_error is not None:
            raise self.purchase_error
        await dto.before_commit()
        self.granted.append(dto.transaction.payment_id)

    async def webhook(self) -> None:
        await self.process_payment.system(
            ProcessPaymentDto(self.dao.transaction.payment_id, TransactionStatus.COMPLETED, GATEWAY)
        )


@pytest.fixture
def env(uow, sample_user_dto, sample_subscription_dto, transaction):
    return Env(uow, sample_user_dto, sample_subscription_dto, FakeTransactionDao(transaction))


def _pending_notifications(env: Env) -> list[Any]:
    return [
        call
        for call in env.notifier.notify_user.await_args_list
        if call.kwargs.get("payload")
        and call.kwargs["payload"].i18n_key == "ntf-gateway.payment-fulfillment-pending"
    ]


# --------------------------------------------------------------------------- fulfilment


@pytest.mark.asyncio
async def test_successful_payment_is_fulfilled_once(env):
    await env.webhook()

    assert env.granted == [env.dao.transaction.payment_id]
    assert env.dao.fulfilled_at is not None
    env.redirect.to_success_payment.assert_awaited_once()
    env.event_publisher.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_purchase_failure_keeps_payment_paid_and_unfulfilled(env):
    env.purchase_error = RuntimeError("API Error USER_NOT_FOUND: User 0 not found (HTTP 404)")

    await env.webhook()  # must not raise: the payment is safe, the retry task takes over

    assert env.dao.status == TransactionStatus.COMPLETED
    assert env.dao.fulfilled_at is None
    assert env.dao.attempts == 1
    assert env.dao.next_retry_at > datetime_now()
    assert "USER_NOT_FOUND" in env.dao.error
    assert env.granted == []
    env.notifier.notify_system.assert_awaited_once()
    assert env.notifier.notify_system.await_args.args[0].i18n_key == "event-payment.purchase-failed"
    assert len(_pending_notifications(env)) == 1
    env.redirect.to_failed_payment.assert_not_awaited()
    env.redirect.to_success_payment.assert_not_awaited()
    env.event_publisher.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_fulfils_after_failure_and_is_idempotent(env):
    env.purchase_error = RuntimeError("panel down")
    await env.webhook()

    # Backoff not elapsed yet: nothing is retried.
    assert await env.retry.system() == 0

    env.purchase_error = RuntimeError("still down")
    env.dao.expire_backoff()
    assert await env.retry.system() == 0
    assert env.dao.attempts == 2
    # Admins and the user are told only once.
    env.notifier.notify_system.assert_awaited_once()
    assert len(_pending_notifications(env)) == 1

    env.purchase_error = None
    env.dao.expire_backoff()
    assert await env.retry.system() == 1
    assert env.granted == [env.dao.transaction.payment_id]
    env.redirect.to_success_payment.assert_awaited_once()

    # Re-running the task, a direct retry or a duplicate webhook never grants again.
    env.dao.expire_backoff()
    assert await env.retry.system() == 0
    assert await env.process_payment.fulfil_pending(env.dao.transaction.payment_id) is False
    await env.webhook()
    assert env.granted == [env.dao.transaction.payment_id]
    assert env.purchase.system.await_count == 3


@pytest.mark.asyncio
async def test_concurrent_retries_grant_once(env):
    env.dao.status = TransactionStatus.COMPLETED
    env.dao.expire_backoff()

    results = await asyncio.gather(
        env.process_payment.fulfil_pending(env.dao.transaction.payment_id),
        env.process_payment.fulfil_pending(env.dao.transaction.payment_id),
    )

    assert sorted(results) == [False, True]
    assert env.granted == [env.dao.transaction.payment_id]


@pytest.mark.asyncio
async def test_already_fulfilled_run_rolls_back_without_failure_notice(env):
    env.dao.status = TransactionStatus.COMPLETED
    env.dao.expire_backoff()
    env.dao.fulfilled_at = None

    async def fulfilled_elsewhere(dto):
        env.dao.fulfilled_at = datetime_now()  # e.g. a slow attempt whose lease expired
        await dto.before_commit()

    env.purchase.system.side_effect = fulfilled_elsewhere

    assert await env.process_payment.fulfil_pending(env.dao.transaction.payment_id) is False
    env.notifier.notify_system.assert_not_awaited()
    env.redirect.to_success_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_crash_before_claim_is_picked_up(env):
    # COMPLETED was committed, then the worker died: no claim, no fulfilment.
    await env.dao.transition_status(
        env.dao.transaction.payment_id, TransactionStatus.COMPLETED, (TransactionStatus.PENDING,)
    )

    assert await env.retry.system() == 0  # grace period for the webhook worker

    env.dao.updated_at = datetime_now() - FULFILLMENT_GRACE - timedelta(seconds=1)
    assert await env.retry.system() == 1
    assert env.granted == [env.dao.transaction.payment_id]


@pytest.mark.asyncio
async def test_worker_crash_mid_attempt_is_picked_up_after_lease(env, monkeypatch):
    env.dao.status = TransactionStatus.COMPLETED
    # Worker claimed the attempt and died before PurchaseSubscription committed.
    assert await env.dao.claim_fulfillment(env.dao.transaction.payment_id, 12, timedelta(minutes=5))

    assert await env.retry.system() == 0  # lease still held

    env.dao.expire_backoff()
    assert await env.retry.system() == 1
    assert env.dao.attempts == 2
    assert env.granted == [env.dao.transaction.payment_id]


@pytest.mark.asyncio
async def test_retries_stop_after_max_attempts(env):
    env.dao.status = TransactionStatus.COMPLETED
    env.dao.attempts = MAX_FULFILLMENT_ATTEMPTS
    env.dao.expire_backoff()

    assert await env.retry.system() == 0
    env.purchase.system.assert_not_awaited()


@pytest.mark.asyncio
async def test_test_payment_is_marked_fulfilled(env):
    env.dao.transaction.is_test = True

    await env.webhook()

    assert env.dao.fulfilled_at is not None
    env.purchase.system.assert_not_awaited()


# --------------------------------------------------------------------------- DAO statements


def _dao(session: Any) -> TransactionDaoImpl:
    return TransactionDaoImpl(session, MagicMock(), MagicMock(), MagicMock())


def _sql(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


@pytest.mark.asyncio
async def test_cancel_old_only_touches_pending_rows():
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=0))

    await _dao(session).cancel_old()

    sql, params = _sql(session.execute.await_args.args[0])
    where = sql.split("WHERE", 1)[1]
    # Only PENDING rows are canceled: paid (COMPLETED) rows, fulfilled or not, are never touched.
    assert where.count("transactions.status") == 1
    assert "transactions.status = %(status_1)s" in where
    assert params["status_1"] == TransactionStatus.PENDING


@pytest.mark.asyncio
async def test_claim_and_mark_are_conditional_updates():
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    dao = _dao(session)
    payment_id = uuid4()

    assert await dao.claim_fulfillment(payment_id, 12, timedelta(minutes=5)) is None
    sql, params = _sql(session.scalar.await_args.args[0])
    assert "transactions.fulfilled_at IS NULL" in sql
    assert "fulfillment_attempts < " in sql
    assert TransactionStatus.COMPLETED in params.values()

    assert await dao.mark_fulfilled(payment_id) is False
    sql, _ = _sql(session.scalar.await_args.args[0])
    assert "transactions.fulfilled_at IS NULL" in sql
    assert "RETURNING" in sql


# --------------------------------------------------------------------------- trial race


def _trial_use_case(uow, user_dao):
    subscription_dao = MagicMock()
    subscription_dao.get_current = AsyncMock(return_value=None)
    subscription_dao.create = AsyncMock()
    subscription_dao.ensure_remna_id_available = AsyncMock()
    remnawave = MagicMock()
    remnawave.resolve_user = AsyncMock(return_value=None)
    remnawave.create_user = AsyncMock(
        return_value=MagicMock(
            id=777,
            status="ACTIVE",
            expire_at=datetime_now() + timedelta(days=3),
            subscription_url="https://sub.example.com/777",
        )
    )
    use_case = ActivateTrialSubscription(
        uow, user_dao, subscription_dao, remnawave, MagicMock(publish=AsyncMock())
    )
    return use_case, subscription_dao, remnawave


@pytest.mark.asyncio
async def test_trial_race_loser_does_not_touch_panel(uow, sample_user_dto, sample_plan_dto):
    sample_user_dto.is_trial_available = True  # stale DTO: the DB flag was already consumed
    user_dao = MagicMock(claim_trial=AsyncMock(return_value=False))
    use_case, subscription_dao, remnawave = _trial_use_case(uow, user_dao)

    with pytest.raises(TrialNotAvailableError):
        await use_case.system(ActivateTrialSubscriptionDto(sample_user_dto, sample_plan_dto))

    user_dao.claim_trial.assert_awaited_once_with(sample_user_dto.id)
    remnawave.create_user.assert_not_awaited()
    remnawave.update_user.assert_not_called()
    subscription_dao.create.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_parallel_trial_activations_grant_once(uow, sample_user_dto, sample_plan_dto):
    sample_user_dto.is_trial_available = True
    state = {"available": True}

    async def claim_trial(user_id: int) -> bool:
        await asyncio.sleep(0)
        claimed, state["available"] = state["available"], False
        return claimed

    user_dao = MagicMock(claim_trial=AsyncMock(side_effect=claim_trial))
    use_case, subscription_dao, remnawave = _trial_use_case(uow, user_dao)
    dto = ActivateTrialSubscriptionDto(sample_user_dto, sample_plan_dto)

    results = await asyncio.gather(
        use_case.system(dto), use_case.system(dto), return_exceptions=True
    )

    assert sum(isinstance(r, TrialNotAvailableError) for r in results) == 1
    remnawave.create_user.assert_awaited_once()
    subscription_dao.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_trial_panel_failure_rolls_back_claim(uow, sample_user_dto, sample_plan_dto):
    sample_user_dto.is_trial_available = True
    user_dao = MagicMock(claim_trial=AsyncMock(return_value=True))
    use_case, subscription_dao, remnawave = _trial_use_case(uow, user_dao)
    remnawave.create_user.side_effect = RuntimeError("panel down")

    with pytest.raises(RuntimeError):
        await use_case.system(ActivateTrialSubscriptionDto(sample_user_dto, sample_plan_dto))

    # The claim ran in the same UoW, which exits with the exception (rollback) and no commit.
    uow.commit.assert_not_awaited()
    assert uow.__aexit__.await_args.args[0] is RuntimeError


# --------------------------------------------------------------------------- trusted proxies

YOOKASSA_IP = "185.71.76.1"


def _yookassa(trusted_proxies: Any = None) -> YookassaGateway:
    gateway = object.__new__(YookassaGateway)
    if trusted_proxies is not None:
        gateway.config = MagicMock(trusted_proxies=trusted_proxies)
    return gateway


def _webhook_request(peer: Optional[str], headers: Optional[dict[str, str]] = None) -> MagicMock:
    request = MagicMock()
    request.headers = Headers(headers or {})
    request.client = MagicMock(host=peer) if peer else None
    return request


@pytest.mark.parametrize("trusted_proxies", [None, [], "", [""]])
def test_yookassa_without_trusted_proxies_keeps_legacy_header_trust(trusted_proxies):
    gateway = _yookassa(trusted_proxies)
    request = _webhook_request("8.8.8.8", {"CF-Connecting-IP": YOOKASSA_IP})

    assert gateway._verify_webhook(request) is True


@pytest.mark.parametrize(
    ("peer", "headers", "expected"),
    [
        # Spoofed header from an untrusted peer is ignored.
        ("8.8.8.8", {"CF-Connecting-IP": YOOKASSA_IP}, False),
        ("8.8.8.8", {"X-Forwarded-For": YOOKASSA_IP}, False),
        # Header from the trusted proxy is honored.
        ("10.1.2.3", {"CF-Connecting-IP": YOOKASSA_IP}, True),
        ("10.1.2.3", {"X-Forwarded-For": f"{YOOKASSA_IP}, 10.0.0.9"}, True),
        ("10.1.2.3", {"X-Forwarded-For": "8.8.8.8"}, False),
        # YooKassa connecting directly (no proxy) is checked by its own address.
        (YOOKASSA_IP, {}, True),
    ],
)
def test_yookassa_trusted_proxies(peer, headers, expected):
    gateway = _yookassa(["10.0.0.0/8"])

    assert gateway._verify_webhook(_webhook_request(peer, headers)) is expected


def test_yookassa_trusted_proxies_without_client_address_is_rejected():
    with pytest.raises(PermissionError):
        _yookassa(["10.0.0.0/8"])._verify_webhook(_webhook_request(None))
