from remnapy.models.webhook import WebhookPayloadDto

from src.web.endpoints.remnawave import _normalize_webhook_payload


def test_normalize_v3_user_created_webhook():
    v3_payload = {
        "event": "user.created",
        "data": {
            "id": 1234,
            "username": "remna_user_3",
            "status": "ACTIVE",
            "trafficLimitStrategy": "NO_RESET",
            "trafficLimitBytes": 10737418240,
            "usedTrafficBytes": 0,
            "lifetimeUsedTrafficBytes": 0,
            "expireAt": "2026-12-31T23:59:59Z",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
    }

    normalized = _normalize_webhook_payload(v3_payload)
    dto = WebhookPayloadDto.from_dict(normalized)
    assert dto.event == "user.created"
    assert dto.data.id == 1234
    assert dto.data.username == "remna_user_3"


def test_normalize_v3_hwid_device_webhook():
    v3_device_payload = {
        "event": "user_hwid_devices.added",
        "data": {
            "user": {
                "id": 888,
                "username": "device_user_3",
                "status": "ACTIVE",
                "trafficLimitStrategy": "NO_RESET",
                "trafficLimitBytes": 0,
                "usedTrafficBytes": 0,
                "lifetimeUsedTrafficBytes": 0,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            "hwidUserDevice": {
                "userId": 888,
                "hwid": "hwid-xyz-999",
                "platform": "ios",
                "osVersion": "18.0",
                "deviceModel": "iPhone",
                "userAgent": "Shadowrocket",
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
        },
    }

    normalized = _normalize_webhook_payload(v3_device_payload)
    dto = WebhookPayloadDto.from_dict(normalized)
    assert dto.event == "user_hwid_devices.added"
    assert dto.data.user.id == 888
    assert dto.data.hwid_user_device.hwid == "hwid-xyz-999"


def test_normalize_v2_webhook_preserved():
    v2_payload = {
        "event": "user.expired",
        "data": {
            "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "id": 500,
            "username": "v2_user",
            "status": "EXPIRED",
            "trafficLimitStrategy": "NO_RESET",
            "trafficLimitBytes": 0,
            "usedTrafficBytes": 100,
            "lifetimeUsedTrafficBytes": 200,
            "expireAt": "2026-01-01T00:00:00Z",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
    }

    normalized = _normalize_webhook_payload(v2_payload)
    dto = WebhookPayloadDto.from_dict(normalized)
    assert dto.event == "user.expired"
    assert str(dto.data.uuid) == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert dto.data.id == 500
