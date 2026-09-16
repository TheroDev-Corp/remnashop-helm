from uuid import UUID

from aiogram import Bot, F, Router
from aiogram.types import Message, PreCheckoutQuery
from dishka import FromDishka
from loguru import logger

from src.application.common.dao import TransactionDao
from src.application.dto import TelegramUserDto
from src.application.use_cases.gateways.commands.payment import ProcessPayment, ProcessPaymentDto
from src.core.enums import PaymentGatewayType, TransactionStatus

router = Router(name=__name__)


PAYABLE_STATUSES = (
    TransactionStatus.PENDING,
    TransactionStatus.FAILED,
    TransactionStatus.CANCELED,
)


@router.pre_checkout_query()
async def on_pre_checkout(
    pre_checkout_query: PreCheckoutQuery,
    user: TelegramUserDto,
    transaction_dao: FromDishka[TransactionDao],
) -> None:
    logger.info(f"{user.log} Initiated a pre-checkout query")
    try:
        payment_id = UUID(pre_checkout_query.invoice_payload)
    except (TypeError, ValueError):
        logger.warning(f"{user.log} Pre-checkout query rejected: invalid payload")
        await pre_checkout_query.answer(ok=False, error_message="Invalid invoice")
        return

    # Invoice links are reusable: refuse paying a transaction that was already processed,
    # otherwise Telegram charges the Stars and ProcessPayment silently drops the payment.
    transaction = await transaction_dao.get_by_payment_id(payment_id)
    if (
        transaction is None
        or transaction.gateway_type != PaymentGatewayType.TELEGRAM_STARS
        or transaction.user_id != user.id
        or transaction.status not in PAYABLE_STATUSES
    ):
        logger.warning(f"{user.log} Pre-checkout query rejected for transaction '{payment_id}'")
        await pre_checkout_query.answer(ok=False, error_message="This invoice is no longer valid")
        return

    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(
    message: Message,
    user: TelegramUserDto,
    bot: Bot,
    process_payment: FromDishka[ProcessPayment],
) -> None:
    payment = message.successful_payment

    if not payment:
        return

    new_status = TransactionStatus.COMPLETED
    if user.is_owner:
        logger.info(f"{user.log} Refunding test payment '{payment.telegram_payment_charge_id}'")
        await bot.refund_star_payment(
            user_id=user.telegram_id,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
        )
        new_status = TransactionStatus.CANCELED
    await process_payment.system(
        ProcessPaymentDto(
            payment_id=UUID(payment.invoice_payload),
            new_transaction_status=new_status,
            gateway_type=PaymentGatewayType.TELEGRAM_STARS,
        )
    )
