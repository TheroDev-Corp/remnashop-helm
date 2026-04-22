import hmac
from typing import Any, cast

import orjson
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Request, Response, status
from loguru import logger
from remnapy.controllers import WebhookUtility
from remnapy.models.webhook import NodeDto, UserDto, UserHwidDeviceEventDto

from src.application.common import EventPublisher
from src.application.events import ErrorEvent
from src.application.services import RemnaWebhookService
from src.core.config import AppConfig
from src.core.constants import API_V1, REMNAWAVE_WEBHOOK_PATH

router = APIRouter(prefix=API_V1)


def _validate_remnawave_signature(
    payload: dict[str, Any],
    signature: str | None,
    secret: str,
) -> bool:
    if not signature:
        return False

    expected_signature = hmac.new(
        key=secret.encode("utf-8"),
        msg=orjson.dumps(payload),
        digestmod="sha256",
    ).hexdigest()
    return hmac.compare_digest(signature, expected_signature)


@router.post(REMNAWAVE_WEBHOOK_PATH)
@inject
async def remnawave_webhook(
    request: Request,
    config: FromDishka[AppConfig],
    remna_webhook_service: FromDishka[RemnaWebhookService],
    event_publisher: FromDishka[EventPublisher],
) -> Response:
    try:
        raw_body = await request.body()
        data = orjson.loads(raw_body)
        if not isinstance(data, dict):
            raise ValueError("Webhook payload must be an object")

        logger.debug(f"Received Remnawave webhook payload: '{data}'")
        secret = config.remnawave.webhook_secret.get_secret_value()
        signature = request.headers.get("X-Remnawave-Signature")

        if not _validate_remnawave_signature(data, signature, secret):
            raise ValueError("Invalid Remnawave webhook signature")
    except Exception as e:
        logger.exception(f"Webhook validation failed with error '{e}'")
        raise HTTPException(status_code=401)

    try:
        if (
            data.get("scope") == "torrent_blocker"
            and data.get("event") == "torrent_blocker.report"
        ):
            await remna_webhook_service.handle_torrent_blocker_event(data)
            return Response(status_code=status.HTTP_200_OK)

        payload = WebhookUtility.parse_webhook(
            body=raw_body.decode("utf-8"),
            headers=dict(request.headers),
            webhook_secret=secret,
            validate=False,
        )

        if not payload:
            logger.warning("Payload is empty after validation")
            raise HTTPException(status_code=401, detail="Unauthorized")

        if WebhookUtility.is_user_event(payload.event):
            user = cast(UserDto, WebhookUtility.get_typed_data(payload))
            await remna_webhook_service.handle_user_event(payload.event, user)

        elif WebhookUtility.is_user_hwid_devices_event(payload.event):
            event = cast(UserHwidDeviceEventDto, WebhookUtility.get_typed_data(payload))
            await remna_webhook_service.handle_device_event(
                payload.event,
                event.user,
                event.hwid_user_device,
            )

        elif WebhookUtility.is_node_event(payload.event):
            node = cast(NodeDto, WebhookUtility.get_typed_data(payload))
            await remna_webhook_service.handle_node_event(payload.event, node)

        else:
            logger.warning(f"Unhandled Remnawave event type '{payload.event}'")

    except Exception as e:
        logger.exception(f"Failed to process Remnawave webhook due to '{e}'")
        error_event = ErrorEvent(**config.build.data, exception=e)
        await event_publisher.publish(error_event)

    return Response(status_code=status.HTTP_200_OK)
