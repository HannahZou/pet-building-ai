"""Time helpers.

* Server timestamps are timezone-aware UTC (``utc_now``). SQLite returns them
  naive, so ``as_utc`` re-attaches UTC when reading.
* Visits are scheduled in the building's local time (``visit_date`` + times);
  each building's IANA time zone lives in ``building_settings``.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import BuildingSetting, Visit


def default_timezone() -> str:
    return os.getenv("PAWPAL_DEFAULT_TIMEZONE", "America/New_York")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def building_tz(session: Session, building_name: str) -> ZoneInfo:
    name = session.scalar(select(BuildingSetting.timezone).where(BuildingSetting.building_name == building_name))
    try:
        return ZoneInfo(name or default_timezone())
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def ensure_building(session: Session, building_name: str, tz: str | None = None) -> None:
    if session.scalar(select(BuildingSetting.id).where(BuildingSetting.building_name == building_name)) is None:
        session.add(BuildingSetting(building_name=building_name, timezone=tz or default_timezone()))
        session.flush()


def visit_tz(session: Session, visit: Visit) -> ZoneInfo:
    return building_tz(session, visit.booking.resident.building_name)


def local_today(tz: ZoneInfo, now: datetime | None = None) -> date:
    return as_utc(now or utc_now()).astimezone(tz).date()


def scheduled_start(visit: Visit, tz: ZoneInfo) -> datetime:
    return datetime.combine(visit.visit_date, visit.start_time, tzinfo=tz)


def scheduled_end(visit: Visit, tz: ZoneInfo) -> datetime:
    return datetime.combine(visit.visit_date, visit.end_time, tzinfo=tz)


def fmt_local(dt: datetime | None, tz: ZoneInfo, with_date: bool = False) -> str | None:
    if dt is None:
        return None
    local = as_utc(dt).astimezone(tz)
    text = local.strftime("%-I:%M %p") if os.name != "nt" else local.strftime("%I:%M %p").lstrip("0")
    return f"{local:%b %d} {text}" if with_date else text
