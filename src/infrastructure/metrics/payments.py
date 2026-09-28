"""Payment webhook outcome counter.

`outcome` is a closed set so the label stays low-cardinality; no payment id or amount is ever
attached.
"""

from enum import StrEnum

from src.core.enums import PaymentGatewayType

from .registry import PAYMENT_WEBHOOKS

UNKNOWN_GATEWAY_LABEL: str = "UNKNOWN"


class WebhookOutcome(StrEnum):
    ACCEPTED = "accepted"
    """Payload parsed and the fulfilment task enqueued (or the ping acknowledged)."""

    IGNORED = "ignored"
    """Technical ping / status the gateway does not want acted on."""

    REJECTED = "rejected"
    """Malformed or unsupported payload."""

    SIGNATURE_INVALID = "signature_invalid"
    """Signature or source IP verification failed."""

    AMOUNT_MISMATCH = "amount_mismatch"
    """Paid amount did not match the transaction."""

    UNKNOWN_GATEWAY = "unknown_gateway"
    """No such gateway, or it is inactive/unconfigured."""

    ERROR = "error"
    """Unexpected failure while handling the webhook."""


def gateway_label(gateway_type: str) -> str:
    """Only real gateways become labels: the path segment is attacker-controlled."""
    try:
        return str(PaymentGatewayType(gateway_type.upper()))
    except ValueError:
        return UNKNOWN_GATEWAY_LABEL


def observe_payment_webhook(gateway_type: str, outcome: WebhookOutcome) -> None:
    PAYMENT_WEBHOOKS.labels(gateway_label(gateway_type), str(outcome)).inc()
