from typing import Optional
from uuid import UUID

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Request, Response, status
from loguru import logger

from src.application.common import EventPublisher
from src.application.common.dao import TransactionDao
from src.application.common.uow import UnitOfWork
from src.application.events import ErrorEvent
from src.application.use_cases.gateways.queries.providers import GetPaymentGatewayInstance
from src.core.config import AppConfig
from src.core.constants import API_V1, PAYMENTS_WEBHOOK_PATH
from src.core.enums import PaymentGatewayType, TransactionStatus
from src.core.exceptions import GatewayNotConfiguredError
from src.infrastructure.payment_gateways import PlategaGateway
from src.infrastructure.payment_gateways.base import BasePaymentGateway
from src.infrastructure.taskiq.tasks.payments import handle_payment_transaction_task

router = APIRouter(prefix=API_V1 + PAYMENTS_WEBHOOK_PATH, include_in_schema=False)


async def _build_response(
    gateway: Optional[BasePaymentGateway], request: Request, gateway_type: str
) -> Response:
    if gateway is not None:
        try:
            return await gateway.build_webhook_response(request)
        except Exception:
            logger.exception(f"Failed to build webhook response for '{gateway_type}'")
    return Response(status_code=status.HTTP_200_OK)


async def _enqueue_payment_task(
    payment_id: UUID,
    payment_status: TransactionStatus,
    gateway_enum: PaymentGatewayType,
    gateway_type: str,
    config: AppConfig,
    event_publisher: EventPublisher,
) -> Optional[Response]:
    try:
        await handle_payment_transaction_task.kiq(payment_id, payment_status, gateway_enum)  # type: ignore[call-overload]
        return None
    except Exception as e:
        logger.exception(f"Failed to enqueue payment task for '{gateway_type}'")
        error_event = ErrorEvent(**config.build.data, exception=e)
        await event_publisher.publish(error_event)
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


async def _verify_paid_amount(
    gateway: BasePaymentGateway,
    request: Request,
    payment_id: UUID,
    transaction_dao: TransactionDao,
    uow: UnitOfWork,
) -> bool:
    async with uow:
        transaction = await transaction_dao.get_by_payment_id(payment_id)
    if transaction is None:
        # ProcessPayment reports unknown transactions itself.
        return True
    return await gateway.verify_paid_amount(request, transaction)


async def _sync_platega_payment_method(
    payment_method: Optional[str],
    payment_id: UUID,
    transaction_dao: TransactionDao,
    uow: UnitOfWork,
) -> None:
    if payment_method is None:
        return

    async with uow:
        transaction = await transaction_dao.get_by_payment_id(payment_id)
        if transaction is None:
            logger.warning(f"Transaction '{payment_id}' not found for Platega payment method sync")
            return

        transaction.payment_method = payment_method
        await transaction_dao.update(transaction)
        await uow.commit()


async def _process_payment_webhook(
    gateway_type: str,
    request: Request,
    config: AppConfig,
    event_publisher: EventPublisher,
    get_payment_gateway_instance: GetPaymentGatewayInstance,
    transaction_dao: TransactionDao,
    uow: UnitOfWork,
) -> Response:
    try:
        gateway_enum = PaymentGatewayType(gateway_type.upper())
    except ValueError:
        logger.exception(f"Invalid gateway type received: '{gateway_type}'")
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    gateway: Optional[BasePaymentGateway] = None
    platega_payment_method: Optional[str] = None
    try:
        gateway = await get_payment_gateway_instance.system(gateway_enum)
        result = await gateway.handle_webhook(request)
        # Gateway instances are shared app-wide: read per-request state before any await.
        if isinstance(gateway, PlategaGateway):
            platega_payment_method = gateway.selected_payment_method
    except GatewayNotConfiguredError:
        logger.warning(f"Webhook received for inactive/unconfigured gateway '{gateway_enum}'")
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    except PermissionError:
        logger.warning(f"Webhook signature verification failed for '{gateway_enum}'")
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    except (ValueError, NotImplementedError) as e:
        # Malformed or unsupported payloads can be sent by anyone: log, but do not alert admins.
        logger.warning(f"Rejected webhook payload for '{gateway_type}': {e}")
        return await _build_response(gateway, request, gateway_type)
    except Exception as e:
        logger.exception(f"Error processing webhook for '{gateway_type}': {e}")
        error_event = ErrorEvent(**config.build.data, exception=e)
        await event_publisher.publish(error_event)
        return await _build_response(gateway, request, gateway_type)

    if result is not None:
        error_response = await _handle_webhook_result(
            result,
            gateway,
            gateway_enum,
            gateway_type,
            request,
            platega_payment_method,
            config,
            event_publisher,
            transaction_dao,
            uow,
        )
        if error_response is not None:
            return error_response

    return await _build_response(gateway, request, gateway_type)


async def _handle_webhook_result(
    result: tuple[UUID, TransactionStatus],
    gateway: BasePaymentGateway,
    gateway_enum: PaymentGatewayType,
    gateway_type: str,
    request: Request,
    platega_payment_method: Optional[str],
    config: AppConfig,
    event_publisher: EventPublisher,
    transaction_dao: TransactionDao,
    uow: UnitOfWork,
) -> Optional[Response]:
    payment_id, payment_status = result
    if payment_status == TransactionStatus.COMPLETED and not await _verify_paid_amount(
        gateway, request, payment_id, transaction_dao, uow
    ):
        logger.warning(f"Paid amount verification failed for '{gateway_enum}' '{payment_id}'")
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    if gateway_enum == PaymentGatewayType.PLATEGA:
        await _sync_platega_payment_method(platega_payment_method, payment_id, transaction_dao, uow)
    return await _enqueue_payment_task(
        payment_id, payment_status, gateway_enum, gateway_type, config, event_publisher
    )


@router.post("/{gateway_type}")
@inject
async def payments_webhook(
    gateway_type: str,
    request: Request,
    config: FromDishka[AppConfig],
    event_publisher: FromDishka[EventPublisher],
    get_payment_gateway_instance: FromDishka[GetPaymentGatewayInstance],
    transaction_dao: FromDishka[TransactionDao],
    uow: FromDishka[UnitOfWork],
) -> Response:
    return await _process_payment_webhook(
        gateway_type=gateway_type,
        request=request,
        config=config,
        event_publisher=event_publisher,
        get_payment_gateway_instance=get_payment_gateway_instance,
        transaction_dao=transaction_dao,
        uow=uow,
    )
