from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..errors import RelayError

_RULE_TYPES = {"daily", "weekly", "monthly", "n_days", "once"}


@dataclass(frozen=True, slots=True)
class Occurrence:
    instant_utc: datetime
    local_time: datetime
    occurrence_key: str


def _invalid(field: str, message: str) -> RelayError:
    return RelayError("SCHEDULE_RULE_INVALID", f"{field}: {message}", details={"field": field})


def _timezone(value: Any) -> ZoneInfo:
    if not isinstance(value, str) or not value:
        raise _invalid("timezone", "an IANA timezone is required")
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError:
        raise _invalid("timezone", f"unknown IANA timezone: {value}") from None


def _clock(value: Any, field: str = "times") -> time:
    if not isinstance(value, str):
        raise _invalid(field, "time must use HH:MM")
    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        raise _invalid(field, "time must use HH:MM") from None
    if parsed.second or parsed.microsecond:
        raise _invalid(field, "seconds are not supported")
    return parsed


def _local_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise _invalid("run_at_local", "a local ISO datetime is required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise _invalid("run_at_local", "invalid ISO datetime") from None
    if parsed.tzinfo is not None:
        raise _invalid("run_at_local", "timezone must be provided separately")
    return parsed.replace(second=0, microsecond=0)


def validate_rule(rule: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(rule, dict):
        raise _invalid("rule", "an object is required")
    rule_type = rule.get("type")
    if rule_type not in _RULE_TYPES:
        raise _invalid("type", "must be daily, weekly, monthly, n_days, or once")
    _timezone(rule.get("timezone"))
    normalized = dict(rule)
    normalized["type"] = rule_type
    normalized["timezone"] = str(rule["timezone"])
    if rule_type == "once":
        normalized["run_at_local"] = _local_datetime(rule.get("run_at_local")).isoformat(timespec="minutes")
        return normalized

    times = rule.get("times")
    if not isinstance(times, list) or not times:
        raise _invalid("times", "at least one time is required")
    parsed_times = sorted({_clock(value).isoformat(timespec="minutes") for value in times})
    normalized["times"] = parsed_times

    if rule_type == "weekly":
        weekdays = rule.get("weekdays")
        if (
            not isinstance(weekdays, list)
            or not weekdays
            or any(not isinstance(day, int) or day not in range(1, 8) for day in weekdays)
        ):
            raise _invalid("weekdays", "must contain ISO weekdays from 1 to 7")
        normalized["weekdays"] = sorted(set(weekdays))
    elif rule_type == "monthly":
        month_days = rule.get("month_days")
        if (
            not isinstance(month_days, list)
            or not month_days
            or any(not isinstance(day, int) or day not in range(1, 32) for day in month_days)
        ):
            raise _invalid("month_days", "must contain days from 1 to 31")
        policy = rule.get("missing_day_policy", "skip")
        if policy not in {"skip", "last_day"}:
            raise _invalid("missing_day_policy", "must be skip or last_day")
        normalized["month_days"] = sorted(set(month_days))
        normalized["missing_day_policy"] = policy
    elif rule_type == "n_days":
        interval = rule.get("interval_days")
        if not isinstance(interval, int) or interval < 1:
            raise _invalid("interval_days", "must be a positive integer")
        try:
            anchor = date.fromisoformat(str(rule.get("anchor_date")))
        except ValueError:
            raise _invalid("anchor_date", "must be an ISO date") from None
        normalized["interval_days"] = interval
        normalized["anchor_date"] = anchor.isoformat()
    return normalized


def _valid_local_instants(local_naive: datetime, zone: ZoneInfo) -> list[tuple[datetime, datetime]]:
    candidates: dict[datetime, datetime] = {}
    for fold in (0, 1):
        local = local_naive.replace(tzinfo=zone, fold=fold)
        instant = local.astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == local_naive:
            candidates[instant] = local
    return sorted(candidates.items(), key=lambda item: item[0])


def _occurrence(local_naive: datetime, zone: ZoneInfo, rule_type: str) -> Occurrence | None:
    instants = _valid_local_instants(local_naive, zone)
    if not instants:
        return None
    instant, aware_local = instants[0]
    return Occurrence(instant, aware_local, f"{rule_type}:{instant.isoformat()}")


def _candidate_dates(normalized: dict[str, Any], start: date):
    rule_type = normalized["type"]
    anchor = date.fromisoformat(normalized["anchor_date"]) if rule_type == "n_days" else None
    for offset in range(0, 3661):
        current = start + timedelta(days=offset)
        if rule_type == "daily":
            yield current
        elif rule_type == "weekly" and current.isoweekday() in normalized["weekdays"]:
            yield current
        elif rule_type == "n_days" and current >= anchor and (current - anchor).days % normalized["interval_days"] == 0:
            yield current
        elif rule_type == "monthly" and current.day in normalized["month_days"]:
            yield current
        elif rule_type == "monthly" and normalized["missing_day_policy"] == "last_day":
            last_day = calendar.monthrange(current.year, current.month)[1]
            if current.day == last_day and any(day > last_day for day in normalized["month_days"]):
                yield current


def next_occurrences(rule: dict[str, Any], after_utc: datetime, limit: int = 5) -> list[Occurrence]:
    normalized = validate_rule(rule)
    if limit < 1 or limit > 100:
        raise _invalid("limit", "must be between 1 and 100")
    if after_utc.tzinfo is None:
        after_utc = after_utc.replace(tzinfo=UTC)
    after_utc = after_utc.astimezone(UTC)
    zone = ZoneInfo(normalized["timezone"])
    found: list[Occurrence] = []
    seen: set[datetime] = set()

    if normalized["type"] == "once":
        local = _local_datetime(normalized["run_at_local"])
        occurrence = _occurrence(local, zone, "once")
        if occurrence and occurrence.instant_utc > after_utc:
            return [occurrence]
        return []

    local_after = after_utc.astimezone(zone)
    for current in _candidate_dates(normalized, local_after.date()):
        for value in normalized["times"]:
            local_naive = datetime.combine(current, time.fromisoformat(value))
            occurrence = _occurrence(local_naive, zone, normalized["type"])
            if not occurrence or occurrence.instant_utc <= after_utc or occurrence.instant_utc in seen:
                continue
            seen.add(occurrence.instant_utc)
            found.append(occurrence)
        found.sort(key=lambda item: item.instant_utc)
        if len(found) >= limit:
            return found[:limit]
    return found[:limit]
