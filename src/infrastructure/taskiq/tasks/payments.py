from uuid import UUID

from dishka.integrations.taskiq import FromDishka, inject

from src.application.use_cases.gateways.commands.payment import (
    ProcessPayment,
    ProcessPaymentDto,
    RetryUnfulfilledPayments,
)
from src.application.use_cases.misc.commands.maintenance import CancelOldTransactions
from src.core.enums import PaymentGatewayType, TransactionStatus
from src.infrastructure.taskiq.broker import broker


@broker.task()
@inject(patch_module=True)
async def handle_payment_transaction_task(
    payment_id: UUID,
    payment_status: TransactionStatus,
    gateway_type: PaymentGatewayType,
    process_payment: FromDishka[ProcessPayment],
) -> None:
    await process_payment.system(
        ProcessPaymentDto(
            payment_id=payment_id,
            new_transaction_status=payment_status,
            gateway_type=gateway_type,
        )
    )


@broker.task(schedule=[{"cron": "*/5 * * * *"}])
@inject(patch_module=True)
async def retry_unfulfilled_payments_task(
    retry_unfulfilled_payments: FromDishka[RetryUnfulfilledPayments],
) -> None:
    # Paid (COMPLETED) transactions whose subscription was never granted: purchase failure or
    # a worker that died between confirming the payment and granting access.
    await retry_unfulfilled_payments.system()


@broker.task(schedule=[{"cron": "*/30 * * * *"}])
@inject(patch_module=True)
async def cancel_old_transactions_task(
    cancel_old_transactions: FromDishka[CancelOldTransactions],
) -> None:
    await cancel_old_transactions.system()
