from datetime import datetime

from src.core.enums import PlanType
from src.core.utils.converters import (
    bytes_to_gb,
    country_code_to_flag,
    days_to_datetime,
    event_to_key,
    gb_to_bytes,
    limits_to_plan_type,
    normalize_channel_id,
    percent,
    user_name_clean,
)
from src.core.utils.i18n_helpers import (
    i18n_format_bytes_to_unit,
    i18n_format_days,
    i18n_format_device_limit,
    i18n_format_seconds,
)
from src.core.utils.i18n_keys import ByteUnitKey, TimeUnitKey, UtilKey


def test_converters_bytes_and_gb():
    assert gb_to_bytes(1) == 1073741824
    assert bytes_to_gb(1073741824) == 1
    assert percent(50, 100) == 50.0
    assert percent(1, 3) == 33.33


def test_user_name_clean():
    assert user_name_clean("<b>Test User</b>", 12345) == "Test User"
    assert user_name_clean(None, 12345) == "12345"
    assert user_name_clean("", 12345) == "12345"


def test_event_to_key():
    assert event_to_key("UserFirstConnectionEvent") == "event-user-first-connection-event"


def test_country_code_to_flag():
    assert country_code_to_flag("RU") == "🇷🇺"
    assert country_code_to_flag("US") == "🇺🇸"
    assert country_code_to_flag("NL") == "🇳🇱"
    assert country_code_to_flag("UNKNOWN") == "🏴‍☠️"


def test_days_to_datetime():
    dt = days_to_datetime(30)
    assert isinstance(dt, datetime)
    assert dt.tzinfo is not None


def test_normalize_channel_id():
    assert normalize_channel_id(123456789) == -100123456789
    assert normalize_channel_id("-100123456789") == -100123456789


def test_limits_to_plan_type():
    assert limits_to_plan_type(10, 2) == PlanType.BOTH
    assert limits_to_plan_type(10, 0) == PlanType.TRAFFIC
    assert limits_to_plan_type(0, 2) == PlanType.DEVICES
    assert limits_to_plan_type(0, 0) == PlanType.UNLIMITED


def test_i18n_format_bytes():
    unit, data = i18n_format_bytes_to_unit(1024 * 1024 * 1024 * 2)
    assert unit == ByteUnitKey.GIGABYTE
    assert data["value"] == 2.0


def test_i18n_format_device_limit():
    key, data = i18n_format_device_limit(None)
    assert key == UtilKey.UNLIMITED
    key, data = i18n_format_device_limit(5)
    assert key == UtilKey.UNIT_DEVICE
    assert data["value"] == 5


def test_i18n_format_days():
    key, data = i18n_format_days(0)
    assert key == UtilKey.UNLIMITED
    key, data = i18n_format_days(30)
    assert key == TimeUnitKey.MONTH
    assert data["value"] == 1
    key, data = i18n_format_days(365)
    assert key == TimeUnitKey.YEAR
    assert data["value"] == 1


def test_i18n_format_seconds():
    parts = i18n_format_seconds(86400 + 3600)
    assert len(parts) == 2
    assert parts[0] == (TimeUnitKey.DAY, {"value": 1})
    assert parts[1] == (TimeUnitKey.HOUR, {"value": 1})
