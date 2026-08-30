"""Exact UTC/Unix timestamp conversions without binary floating point."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta


UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
NANOSECONDS_PER_SECOND = 1_000_000_000
NANOSECONDS_PER_DAY = 86_400 * NANOSECONDS_PER_SECOND


def utc_datetime_to_ns(value: datetime) -> int:
    """Return exact Unix nanoseconds for an aware UTC ``datetime``."""

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be an aware UTC datetime")
    delta = value - UNIX_EPOCH
    return (
        delta.days * NANOSECONDS_PER_DAY
        + delta.seconds * NANOSECONDS_PER_SECOND
        + delta.microseconds * 1_000
    )


def utc_datetime_to_ms(value: datetime) -> int:
    """Return Unix milliseconds using integer arithmetic, flooring sub-ms input."""

    return utc_datetime_to_ns(value) // 1_000_000


def unix_ns_to_utc_datetime(value: int) -> datetime:
    """Convert exact, microsecond-representable Unix nanoseconds to UTC."""

    if type(value) is not int:
        raise TypeError("Unix nanoseconds must be an integer")
    if value % 1_000:
        raise ValueError("Unix nanoseconds are not representable at datetime microsecond precision")
    return UNIX_EPOCH + timedelta(microseconds=value // 1_000)


def unix_ms_to_utc_datetime(value: int) -> datetime:
    """Convert integer Unix milliseconds to an aware UTC ``datetime``."""

    if type(value) is not int:
        raise TypeError("Unix milliseconds must be an integer")
    return UNIX_EPOCH + timedelta(milliseconds=value)
