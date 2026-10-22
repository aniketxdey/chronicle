"""Time parsing shared by the API and storage."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_REL = re.compile(r"^(\d+)\s*([mhdw])\s*ago$")


def utc_iso(dt: datetime | None = None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        raise ValueError("naive datetimes are not accepted")
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)")


def parse_duration(value: str | int | float | None) -> int | None:
    """Parse "90s", "30m", "1h30m", "2 hours", or a bare number of minutes into seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    raw = str(value).strip().lower()
    if raw in {"0", "none", "forever", "until stopped", "open"}:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        return int(float(raw) * 60)
    total = 0.0
    for amount, unit in _DURATION.findall(raw):
        number = float(amount)
        if unit.startswith("s"):
            total += number
        elif unit.startswith("m"):
            total += number * 60
        elif unit.startswith("h"):
            total += number * 3600
        else:
            total += number * 86400
    if total <= 0:
        raise ValueError(f"could not parse duration {value!r}; try 30m, 2h, or 1h30m")
    return int(total)


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return "0m"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def parse_time(value: str | None, tz=None) -> str | None:
    """Parse an ISO timestamp or a relative phrase into a UTC ISO string."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    zone = tz or datetime.now().astimezone().tzinfo
    lowered = raw.lower()
    now = datetime.now(zone)
    if lowered == "now":
        return utc_iso(now)
    if lowered == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return utc_iso(start)
    if lowered == "yesterday":
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return utc_iso(start)
    match = _REL.fullmatch(lowered)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        delta = {
            "m": timedelta(minutes=amount),
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
            "w": timedelta(weeks=amount),
        }[unit]
        return utc_iso(now - delta)
    try:
        cleaned = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError(f"could not parse time {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return utc_iso(parsed)
