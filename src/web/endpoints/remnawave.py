import json
from typing import Any, cast

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Request, Response, status
from loguru import logger
from remnapy.controllers import WebhookUtility
from remnapy.models.webhook import (
    NodeDto,
    TorrentBlockerReportDto,
    UserDto,
    UserHwidDeviceEventDto,
    WebhookPayloadDto,
)

from src.application.common import EventPublisher
from src.application.events import ErrorEvent
from src.application.services import RemnaWebhookService
from src.core.config import AppConfig
from src.core.constants import API_V1, REMNAWAVE_WEBHOOK_PATH
from src.infrastructure.services.remnawave import RemnawaveImpl

router = APIRouter(prefix=API_V1, include_in_schema=False)


def _normalize_webhook_payload(payload_dict: dict[str, Any]) -> dict[str, Any]:
    """Normalizes RemnaWave webhook payload for compatibility with v2/v3 schemas."""
    event = payload_dict.get("event", "")
    data = payload_dict.get("data")
    if not isinstance(data, dict):
        return payload_dict

    data = dict(data)
    if event.startswith("user."):
        data = RemnawaveImpl._normalize_user_dict(data)
    elif event.startswith("user_hwid_devices."):
        user = dict(data.get("user", {})) if isinstance(data.get("user"), dict) else {}
        user = RemnawaveImpl._normalize_user_dict(user)
        data["user"] = user

        hwid_device = (
            dict(data.get("hwidUserDevice", {}))
            if isinstance(data.get("hwidUserDevice"), dict)
            else {}
        )
        if "userUuid" not in hwid_device and "user_uuid" not in hwid_device:
            user_id = hwid_device.get("userId") or hwid_device.get("user_id") or 0
            dev_user_uuid = user.get("uuid") or f"00000000-0000-0000-0000-{int(user_id):012d}"
            hwid_device["userUuid"] = dev_user_uuid
        data["hwidUserDevice"] = hwid_device

    normalized = dict(payload_dict)
    if "timestamp" not in normalized or not normalized["timestamp"]:
        normalized["timestamp"] = (
            normalized.get("createdAt")
            or (data.get("createdAt") if isinstance(data, dict) else None)
            or "2026-01-01T00:00:00Z"
        )
    normalized["data"] = data
    return normalized


async def _process_remnawave_webhook(
    request: Request,
    config: AppConfig,
    remna_webhook_service: RemnaWebhookService,
    event_publisher: EventPublisher,
) -> Response:
    try:
        raw_body = await request.body()
        body_str = raw_body.decode("utf-8")
        logger.debug(f"Received Remnawave webhook raw body: '{body_str[:500]}'")

        secret = config.remnawave.webhook_secret.get_secret_value()
        is_valid = WebhookUtility.validate_webhook_with_headers(
            body_str, dict(request.headers), secret
        )
        if not is_valid:
            logger.warning("Webhook validation failed: signature mismatch")
            raise HTTPException(status_code=401, detail="Unauthorized")

        raw_dict = json.loads(body_str)
        normalized_dict = _normalize_webhook_payload(raw_dict)
        payload = WebhookPayloadDto.from_dict(normalized_dict)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Webhook processing/validation failed with error '{e}'")
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not payload:
        logger.warning("Payload is empty after validation")
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
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

        elif WebhookUtility.is_torrent_blocker_event(payload.event):
            report = cast(TorrentBlockerReportDto, WebhookUtility.get_typed_data(payload))
            await remna_webhook_service.handle_torrent_blocker_event(report)

        elif payload.event.startswith("crm.") or payload.event.startswith("service."):
            logger.info(f"Received Remnawave system/crm event '{payload.event}', acknowledged")

        else:
            logger.warning(f"Unhandled Remnawave event type '{payload.event}'")

    except Exception as e:
        logger.exception(f"Failed to process Remnawave webhook due to '{e}'")
        error_event = ErrorEvent(**config.build.data, exception=e)
        await event_publisher.publish(error_event)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    return Response(status_code=status.HTTP_200_OK)


@router.post(REMNAWAVE_WEBHOOK_PATH)
@inject
async def remnawave_webhook(
    request: Request,
    config: FromDishka[AppConfig],
    remna_webhook_service: FromDishka[RemnaWebhookService],
    event_publisher: FromDishka[EventPublisher],
) -> Response:
    return await _process_remnawave_webhook(
        request=request,
        config=config,
        remna_webhook_service=remna_webhook_service,
        event_publisher=event_publisher,
    )
