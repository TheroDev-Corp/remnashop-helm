from src.application.common import Interactor
from src.application.common.dao import PaymentGatewayDao
from src.application.common.uow import UnitOfWork
from src.application.dto import UserDto
from src.core.enums import PaymentGatewayType
from src.core.exceptions import GatewayNotConfiguredError
from src.infrastructure.payment_gateways import BasePaymentGateway, PaymentGatewayFactory


class GetPaymentGatewayInstance(Interactor[PaymentGatewayType, BasePaymentGateway]):
    required_permission = None

    def __init__(
        self,
        uow: UnitOfWork,
        payment_gateway_dao: PaymentGatewayDao,
        gateway_factory: PaymentGatewayFactory,
    ) -> None:
        self.uow = uow
        self.payment_gateway_dao = payment_gateway_dao
        self.gateway_factory = gateway_factory

    async def _execute(
        self,
        actor: UserDto,
        gateway_type: PaymentGatewayType,
    ) -> BasePaymentGateway:
        gateway = await self.payment_gateway_dao.get_by_type(gateway_type)

        if not gateway:
            raise ValueError(f"Payment gateway of type '{gateway_type}' not found")

        if not gateway.is_active:
            raise GatewayNotConfiguredError(f"Gateway '{gateway_type}' is not active")

        if gateway.settings is not None and not gateway.settings.is_configured:
            raise GatewayNotConfiguredError(f"Gateway '{gateway_type}' is not configured")

        return self.gateway_factory(gateway)
