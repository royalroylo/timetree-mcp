"""Argument validation helpers for read-only TimeTree operations."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo


def calendar_id_value(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("calendar_id must be a string or integer")
    result = str(value).strip()
    if not result:
        raise ValueError("calendar_id must not be empty")
    return result


def _timezone(name: str | None) -> timezone | ZoneInfo:
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001 - surface a stable input error
        raise ValueError(f"Unknown timezone: {name}") from exc


def to_epoch_millis(value: Any, timezone_name: str = "UTC") -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("time must be an ISO-8601 string or Unix milliseconds")

    text = value.strip()
    try:
        if len(text) == 10:
            parsed = datetime.combine(date.fromisoformat(text), time.min)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 time: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_timezone(timezone_name))
    return int(parsed.timestamp() * 1000)
