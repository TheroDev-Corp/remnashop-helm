from datetime import datetime
from typing import Optional, Union


class MenuRenderError(Exception): ...


class PermissionDeniedError(Exception): ...


class UserNotFoundError(Exception):
    def __init__(self, user_id: Union[int, str, None] = None) -> None:
        self.user_id = user_id
        super().__init__(f"User with id '{user_id}' not found" if user_id else "User not found")


class RemnaUserBindingError(ValueError):
    """Raised when a bot user would be bound to a Remnawave user that belongs to someone else."""


class RemnawaveActionError(ValueError):
    """The panel rejected a user action (e.g. enable/disable) with a client error."""

    def __init__(
        self,
        action: str,
        remna_id: int,
        status_code: int,
        code: Optional[str],
        message: Optional[str],
    ) -> None:
        self.action = action
        self.remna_id = remna_id
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(
            f"Remnawave refused action '{action}' for RemnaUser '{remna_id}': "
            f"HTTP {status_code}, code '{code}', message '{message}'"
        )


class FileNotFoundError(Exception): ...


class LogsToFileDisabledError(Exception):
    def __init__(self) -> None:
        super().__init__("Logging to file is disabled in configuration")


class PlanError(Exception): ...


class SquadsEmptyError(PlanError): ...


class TrialDurationError(PlanError): ...


class PlanNameAlreadyExistsError(PlanError): ...


class UserAlreadyAllowedError(PlanError): ...


class DurationAlreadyExistsError(PlanError): ...


class PriceNotFoundError(PlanError): ...


class GatewayNotConfiguredError(Exception): ...


class PurchaseError(Exception): ...


class TrialNotAvailableError(Exception): ...


class MenuEditorInvalidPayloadError(Exception): ...


class BlacklistSourceAlreadyExistsError(Exception): ...


class CooldownError(Exception):
    def __init__(self, available_at: datetime) -> None:
        self.available_at = available_at
        super().__init__(f"Cooldown active until {available_at}")


class PromocodeError(Exception): ...


class PromocodeNotFoundError(PromocodeError): ...


class PromocodeNotAvailableError(PromocodeError): ...


class PromocodeExpiredError(PromocodeNotAvailableError): ...


class PromocodeAlreadyActivatedError(PromocodeError): ...


class EmailDeliveryError(Exception): ...


class EmailDeliveryDisabledError(Exception): ...
