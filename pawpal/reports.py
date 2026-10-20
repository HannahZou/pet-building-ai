"""Read-only queries for the dashboards (no Streamlit imports here)."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .availability import availability_map, time_off_map
from .models import Booking, BookingStatus, Caregiver, Resident, Visit, VisitStatus
from .pricing import from_cents, quote_rows
from .scheduling import check_booking_schedule
from .workflow import STATUS_LABELS

PIPELINE = (
    BookingStatus.QUOTED,
    BookingStatus.PENDING_RESIDENT_APPROVAL,
    BookingStatus.PENDING_ADMIN_APPROVAL,
    BookingStatus.NEEDS_RESCHEDULING,
)


def status_counts(session: Session, building: str | None = None) -> dict[str, int]:
    q = select(Booking.status).join(Resident, Booking.resident_id == Resident.id)
    if building:
        q = q.where(Resident.building_name == building)
    counts = Counter(session.scalars(q))
    return {STATUS_LABELS[s]: counts.get(s, 0) for s in STATUS_LABELS}


def visits_in_range(session: Session, start: date, end: date, building: str | None = None) -> list[dict]:
    q = (
        select(Visit, Booking, Resident)
        .join(Booking, Visit.booking_id == Booking.id)
        .join(Resident, Booking.resident_id == Resident.id)
        .where(Visit.visit_date.between(start, end), Visit.assignment_status != VisitStatus.CANCELLED)
        .order_by(Visit.visit_date, Visit.start_time)
    )
    if building:
        q = q.where(Resident.building_name == building)
    return [
        {
            "Date": v.visit_date,
            "Time": f"{v.start_time:%H:%M}–{v.end_time:%H:%M}",
            "Building": r.building_name,
            "Apt": r.apartment_number,
            "Pet": f"{b.pet.name} ({b.pet.species})",
            "Service": v.service_type.value.replace("_", " "),
            "Caregiver": v.caregiver.full_name if v.caregiver else "Unassigned",
            "Visit status": v.assignment_status.value.title(),
            "Booking": b.id,
            "Booking status": STATUS_LABELS[b.status],
        }
        for v, b, r in session.execute(q).all()
    ]


def availability_grid(session: Session, start: date, days: int = 14) -> list[dict]:
    """One row per active caregiver, one column per date: hours, OFF or blank."""
    caregivers = list(session.scalars(select(Caregiver).where(Caregiver.active_status.is_(True)).order_by(Caregiver.full_name)))
    end = start + timedelta(days=days - 1)
    ids = [c.id for c in caregivers]
    avail = availability_map(session, ids, start, end)
    offs = time_off_map(session, ids, start, end)
    rows = []
    for c in caregivers:
        row = {"Caregiver": c.full_name}
        for i in range(days):
            d = start + timedelta(days=i)
            if any(t.start_date <= d <= t.end_date for t in offs[c.id]):
                cell = "OFF"
            else:
                cell = " / ".join(f"{b.start:%H}-{b.end:%H}" for b in avail[c.id].get(d, [])) or "·"
            row[f"{d:%a %d}"] = cell
        rows.append(row)
    return rows


def pricing_rows(session: Session, building: str | None = None) -> list[dict]:
    q = select(Booking).join(Resident, Booking.resident_id == Resident.id).order_by(Booking.start_date)
    if building:
        q = q.where(Resident.building_name == building)
    out = []
    for b in session.scalars(q):
        quote = b.current_quote
        if not quote:
            continue
        out.append({
            "Booking": b.id,
            "Pet": f"{b.pet.name} ({b.pet.species})",
            "Visits": quote.inputs.get("visits"),
            "60-min": quote.inputs.get("visits_60min"),
            "Add-ons": ", ".join(quote.inputs.get("additional_services", [])) or "—",
            "Visit subtotal": f"${from_cents(quote.visit_subtotal_cents):,.2f}",
            "Add-on fees": f"${from_cents(quote.additional_fees_cents):,.2f}",
            "Total": f"${from_cents(quote.total_cents):,.2f}",
            "Price book": quote.pricing_version,
            "Status": STATUS_LABELS[b.status],
            "_lines": quote_rows(quote),
        })
    return out


def unresolved_conflicts(session: Session, building: str | None = None) -> list[dict]:
    """Every problem on bookings still in the approval pipeline (hard and soft)."""
    q = (
        select(Booking).join(Resident, Booking.resident_id == Resident.id)
        .where(Booking.status.in_(PIPELINE)).order_by(Booking.start_date)
    )
    if building:
        q = q.where(Resident.building_name == building)
    out = []
    for b in session.scalars(q):
        for c in check_booking_schedule(session, b, include_soft=True):
            out.append({
                "Booking": b.id,
                "Status": STATUS_LABELS[b.status],
                "Building": b.resident.building_name,
                "Date": c.day,
                "Time": f"{c.start:%H:%M}–{c.end:%H:%M}",
                "Problem": c.kind.replace("_", " "),
                "Severity": "Blocking" if c.hard else "Warning",
                "Details": c.message,
            })
    return out
