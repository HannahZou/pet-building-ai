"""Centralised KPI calculations.

Every KPI returns a ``Metric`` with its numerator and denominator, so a value can
always be traced back to records. Zero denominators give ``value=None`` with
status ``no_data`` (never 0% or 100%). ``KPI_DEFINITIONS`` is the single source
for docs/KPI_DEFINITIONS.md and the dashboard tooltips.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access import Scope, analytics_scope
from ..actors import Actor
from ..models import BookingStatus as S, EvaluationRun, ExecutionStatus as E, ReportStatus
from ..timeutil import as_utc, building_tz
from .data import (
    AnalyticsFilter, BookingRow, VisitRow, caregiver_assigned_minutes, caregiver_capacity, last_checkout,
    load_bookings, load_email_notifications, load_reports, load_visits, rejected_report_ids,
)

CONFIRMED = (S.CONFIRMED, S.COMPLETED)
LATE_CHECKIN_MINUTES = 30
MIN_RETENTION_COHORT = 5


@dataclass(frozen=True)
class Definition:
    label: str
    formula: str
    numerator: str
    denominator: str
    window: str
    exclusions: str
    missing: str
    unit: str


KPI_DEFINITIONS: dict[str, Definition] = {
    "total_bookings": Definition(
        "Total bookings", "count(bookings)", "Submitted bookings (any status except Draft)", "—",
        "Booking start date in the period", "Drafts (incomplete requests)", "0 when there are none", "count"),
    "confirmed_bookings": Definition(
        "Confirmed bookings", "count(bookings in Confirmed or Completed)", "Bookings now Confirmed or Completed", "—",
        "Booking start date in the period", "Cancelled after confirmation, rejected", "0 when there are none", "count"),
    "completed_bookings": Definition(
        "Completed bookings", "count(bookings in Completed)", "Bookings now Completed", "—",
        "Booking start date in the period", "—", "0 when there are none", "count"),
    "gross_booking_value": Definition(
        "Gross booking value", "Σ current quote total of confirmed + completed bookings",
        "Current quote totals (USD)", "—", "Booking start date in the period",
        "Bookings without a quote (Week 1 legacy records) - counted in the note", "0 when there are none", "usd"),
    "average_booking_value": Definition(
        "Average booking value", "GBV / confirmed bookings with a quote", "Gross booking value",
        "Confirmed + completed bookings that have a quote", "Booking start date in the period",
        "Bookings without a quote", "No data when the denominator is 0", "usd"),
    "booking_conversion_rate": Definition(
        "Booking conversion rate", "Confirmed bookings / valid booking requests",
        "Requests that reached Confirmed (now Confirmed or Completed, or with a confirmation event)",
        "Valid requests: every booking except Drafts", "Request (created) date in the period",
        "Drafts", "No data when the denominator is 0", "pct"),
    "total_scheduled_visits": Definition(
        "Scheduled visits", "count(visits)", "Visits of confirmed bookings", "—", "Visit date in the period",
        "Cancelled visits; visits of unconfirmed bookings", "0 when there are none", "count"),
    "completed_visits": Definition(
        "Completed visits", "count(due visits with a check-out)", "Due visits checked out (Completed or Needs Review)",
        "—", "Visit date in the period and before today", "—", "0 when there are none", "count"),
    "missed_visits": Definition(
        "Missed visits", "count(due visits marked Missed)", "Visits with execution status Missed", "—",
        "Visit date in the period and before today", "—", "0 when there are none", "count"),
    "visit_completion_rate": Definition(
        "Visit completion rate", "Completed visits / visits due", "Due visits with a check-out",
        "Visits due: scheduled visits dated before today (local)", "Visit date in the period",
        "Today's and future visits", "No data when no visit is due", "pct"),
    "caregiver_utilization_rate": Definition(
        "Caregiver utilization", "Assigned care minutes / available care minutes",
        "Minutes of confirmed/completed visits assigned to the caregivers",
        "Working-hour minutes of active caregivers covering the selected buildings, minus time off",
        "Visit / work date in the period", "Inactive caregivers; caregivers with no working hours",
        "No data when no working hours are recorded", "pct"),
    "scheduling_conflict_rate": Definition(
        "Scheduling conflict rate", "Scheduling attempts with conflicts / total scheduling attempts",
        "Attempts that ended in Needs Rescheduling",
        "Attempts: each matcher run (Quoted → next status) and each final confirmation check",
        "Request (created) date in the period", "Bookings never quoted", "No data when there are no attempts", "pct"),
    "avg_booking_processing_hours": Definition(
        "Average booking processing time", "mean(confirmation time − booking creation time)",
        "Σ hours from creation to the Confirmed event", "Bookings with a Confirmed event",
        "Request (created) date in the period", "Bookings never confirmed; legacy records without events",
        "No data when no booking was confirmed", "hours"),
    "avg_assignment_hours": Definition(
        "Average caregiver assignment time", "mean(first full assignment − booking creation)",
        "Σ hours from creation to the first Pending Resident Approval event (every visit had a caregiver)",
        "Bookings that reached that event", "Request (created) date in the period",
        "Bookings never fully assigned", "No data when none", "hours"),
    "repeat_booking_rate": Definition(
        "Repeat booking rate", "Residents with ≥2 confirmed bookings / residents with ≥1",
        "Residents with two or more confirmed/completed bookings", "Residents with at least one",
        "Booking start date in the period", "—", "No data when no resident booked", "pct"),
    "resident_retention_rate": Definition(
        "Resident retention", "Residents active in the previous period who booked again / residents active in the previous period",
        "Previous-period residents with a confirmed booking this period",
        "Residents with a confirmed booking in the previous period of equal length",
        "Current and previous period", "—",
        f"Insufficient data when fewer than {MIN_RETENTION_COHORT} residents were active in the previous period", "pct"),
    "avg_report_delivery_hours": Definition(
        "Average care report delivery time", "mean(report approval − last check-out of the day)",
        "Σ hours from the day's last check-out to approval", "Approved reports with at least one check-out",
        "Report date in the period", "Reports not approved; days without check-outs", "No data when none", "hours"),
    "notification_success_rate": Definition(
        "Notification delivery success", "Emails sent / emails attempted", "Email notifications delivered",
        "Email notifications sent or failed (pending excluded)", "Notification created in the period",
        "In-app notifications (stored, always delivered); pending emails", "No data when no email was attempted", "pct"),
    "customer_satisfaction": Definition(
        "Customer satisfaction", "Average rating from resident feedback", "—", "—", "—", "—",
        "Not available: PawPal does not collect feedback yet - never estimated", "score"),
    "report_unsupported_claim_rate": Definition(
        "Care report unsupported claim rate (published)", "Unsupported factual claims / evaluated factual claims",
        "Claims in stored reports not supported by visit records", "Claims checked by the rule-based grounding check",
        "Report date in the period", "—", "No data when no claims were checked", "pct"),
    "ai_draft_unsupported_claim_rate": Definition(
        "AI draft unsupported claim rate", "Unsupported claims / evaluated claims in AI drafts",
        "Unsupported claims in what the model wrote (before fallback)", "Claims checked in AI drafts",
        "Report date in the period", "Template-only reports", "No data when no AI drafts exist", "pct"),
    "missing_activity_detection": Definition(
        "Missing activity detection", "Surfaced missing items / missing items in records",
        "Missed visits and incomplete essentials shown in the report",
        "Missed visits and incomplete essentials in the records", "Report date in the period", "—",
        "No data when nothing was missing", "pct"),
    "human_correction_rate": Definition(
        "Human correction rate", "Reports rejected at least once / reviewed reports",
        "Reports a manager rejected (sent back) at least once", "Reports approved or rejected",
        "Report date in the period", "Reports still pending review", "No data when none were reviewed", "pct"),
    "booking_extraction_accuracy": Definition(
        "AI extraction accuracy", "Correctly extracted fields / evaluated fields",
        "Benchmark fields matching the hand-written expected value", "Fields evaluated in the latest benchmark run",
        "Latest stored run (not period-bound)", "—", "No data until a benchmark run is stored", "pct"),
}


@dataclass
class Metric:
    key: str
    value: float | None
    numerator: float | None = None
    denominator: float | None = None
    status: str = "ok"  # ok | no_data | insufficient_data | not_available
    note: str = ""

    @property
    def label(self) -> str:
        return KPI_DEFINITIONS[self.key].label

    @property
    def unit(self) -> str:
        return KPI_DEFINITIONS[self.key].unit

    def display(self) -> str:
        if self.value is None:
            return {"no_data": "No data", "insufficient_data": "Insufficient data",
                    "not_available": "Not available"}.get(self.status, "—")
        if self.unit == "pct":
            return f"{self.value:.1%}"
        if self.unit == "usd":
            return f"${self.value:,.2f}"
        if self.unit == "hours":
            return f"{self.value:,.1f} h"
        return f"{self.value:,.0f}"

    def to_row(self) -> dict[str, Any]:
        d = KPI_DEFINITIONS[self.key]
        return {"key": self.key, "metric": d.label, "value": self.display(),
                "raw_value": None if self.value is None else round(self.value, 4),
                "numerator": self.numerator, "denominator": self.denominator, "status": self.status,
                "formula": d.formula, "window": d.window, "note": self.note}


def ratio(key: str, num: float, den: float, note: str = "") -> Metric:
    if not den:
        return Metric(key, None, num, den, "no_data", note or "Denominator is zero.")
    return Metric(key, num / den, num, den, "ok", note)


def average(key: str, values: list[float], note: str = "") -> Metric:
    if not values:
        return Metric(key, None, 0, 0, "no_data", note or "No qualifying records.")
    return Metric(key, mean(values), round(sum(values), 3), len(values), "ok", note)


def count(key: str, n: float, note: str = "") -> Metric:
    return Metric(key, n, n, None, "ok", note)


def _hours(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600


# ================================================================ calculators
def booking_metrics(bookings: list[BookingRow]) -> dict[str, Metric]:
    submitted = [b for b in bookings if b.status != S.DRAFT]
    confirmed = [b for b in submitted if b.status in CONFIRMED]
    priced = [b for b in confirmed if b.total_cents is not None]
    gbv = sum(b.total_cents for b in priced) / 100
    unpriced = len(confirmed) - len(priced)
    note = f"{unpriced} confirmed booking(s) without a quote excluded (Week 1 legacy)." if unpriced else ""
    return {
        "total_bookings": count("total_bookings", len(submitted)),
        "confirmed_bookings": count("confirmed_bookings", len(confirmed)),
        "completed_bookings": count("completed_bookings", sum(b.status == S.COMPLETED for b in submitted)),
        "gross_booking_value": Metric("gross_booking_value", gbv, gbv, None, "ok", note),
        "average_booking_value": ratio("average_booking_value", gbv, len(priced), note),
    }


def request_metrics(requests: list[BookingRow]) -> dict[str, Metric]:
    """Metrics keyed to the request (created) date."""
    valid = [b for b in requests if b.status != S.DRAFT]
    converted = [b for b in valid if b.status in CONFIRMED or b.confirmed_at is not None]
    attempts = sum(b.scheduling_attempts for b in valid)
    conflicts = sum(b.scheduling_conflicts for b in valid)
    processing = [h for b in valid if (h := _hours(b.created_at, b.confirmed_at)) is not None and h >= 0]
    assignment = [h for b in valid if (h := _hours(b.created_at, b.assigned_at)) is not None and h >= 0]
    legacy = sum(1 for b in valid if b.status in CONFIRMED and b.confirmed_at is None)
    return {
        "booking_conversion_rate": ratio("booking_conversion_rate", len(converted), len(valid)),
        "scheduling_conflict_rate": ratio("scheduling_conflict_rate", conflicts, attempts),
        "avg_booking_processing_hours": average(
            "avg_booking_processing_hours", processing,
            f"{legacy} legacy booking(s) without workflow events excluded." if legacy else ""),
        "avg_assignment_hours": average("avg_assignment_hours", assignment),
    }


def visit_metrics(visits: list[VisitRow], as_of: datetime) -> dict[str, Metric]:
    due = [v for v in visits if v.visit_date < as_utc(as_of).astimezone(v.tz).date()]
    done = [v for v in due if v.execution in (E.COMPLETED, E.NEEDS_REVIEW) and v.check_out]
    missed = [v for v in due if v.execution == E.MISSED]
    return {
        "total_scheduled_visits": count("total_scheduled_visits", len(visits)),
        "completed_visits": count("completed_visits", len(done)),
        "missed_visits": count("missed_visits", len(missed)),
        "visit_completion_rate": ratio("visit_completion_rate", len(done), len(due),
                                       f"{len(due) - len(done) - len(missed)} due visit(s) not checked out and not marked missed."
                                       if len(due) - len(done) - len(missed) else ""),
    }


def late_check_ins(visits: list[VisitRow], minutes: int = LATE_CHECKIN_MINUTES) -> list[VisitRow]:
    return [v for v in visits if v.check_in and v.check_in > v.scheduled_start + timedelta(minutes=minutes)]


def customer_metrics(bookings: list[BookingRow], previous: list[BookingRow]) -> dict[str, Metric]:
    per_resident = Counter(b.resident_id for b in bookings if b.status in CONFIRMED)
    repeat = ratio("repeat_booking_rate", sum(1 for n in per_resident.values() if n >= 2), len(per_resident))
    prev_residents = {b.resident_id for b in previous if b.status in CONFIRMED}
    if len(prev_residents) < MIN_RETENTION_COHORT:
        retention = Metric("resident_retention_rate", None, None, len(prev_residents), "insufficient_data",
                           f"Only {len(prev_residents)} resident(s) active in the previous period "
                           f"(minimum {MIN_RETENTION_COHORT}).")
    else:
        retention = ratio("resident_retention_rate", len(prev_residents & set(per_resident)), len(prev_residents))
    return {
        "repeat_booking_rate": repeat,
        "resident_retention_rate": retention,
        "customer_satisfaction": Metric("customer_satisfaction", None, None, None, "not_available",
                                        "No resident feedback is collected yet; satisfaction is not estimated."),
    }


# ================================================================== snapshot
@dataclass
class KPISnapshot:
    filter: AnalyticsFilter
    scope_buildings: list[str] | None
    as_of: datetime
    metrics: dict[str, Metric]
    revenue_by_building: dict[str, float]
    revenue_by_service: dict[str, float]
    weekly: list[dict[str, Any]]
    building_table: list[dict[str, Any]]
    caregiver_table: list[dict[str, Any]]
    weekday_visits: dict[str, int]
    coverage: dict[str, Any]

    def metric_rows(self) -> list[dict[str, Any]]:
        return [m.to_row() for m in self.metrics.values()]


def compute_snapshot(session: Session, actor: Actor, f: AnalyticsFilter, as_of: datetime | None = None) -> KPISnapshot:
    scope = analytics_scope(session, actor, f.buildings)
    as_of = as_utc(as_of) or datetime.now(timezone.utc)
    bookings = load_bookings(session, scope, f, "start")
    requests = load_bookings(session, scope, f, "created")
    previous = load_bookings(session, scope, f.previous(), "start")
    visits = load_visits(session, scope, f)

    metrics: dict[str, Metric] = {}
    metrics.update(booking_metrics(bookings))
    metrics.update(request_metrics(requests))
    metrics.update(visit_metrics(visits, as_of))
    metrics.update(customer_metrics(bookings, previous))

    capacity = caregiver_capacity(session, scope, f)
    assigned = caregiver_assigned_minutes(session, list(capacity), f)
    with_hours = {cid: c for cid, c in capacity.items() if c["available_minutes"] > 0}
    if not visits:
        metrics["caregiver_utilization_rate"] = Metric(
            "caregiver_utilization_rate", None, 0, sum(c["available_minutes"] for c in with_hours.values()),
            "no_data", "No visits for this data source and period.")
    else:
        metrics["caregiver_utilization_rate"] = ratio(
            "caregiver_utilization_rate", sum(assigned.get(cid, 0) for cid in with_hours),
            sum(c["available_minutes"] for c in with_hours.values()),
            "Caregivers may serve several buildings; utilization reflects their whole schedule.")

    reports = load_reports(session, scope, f)
    metrics.update(report_metrics(session, reports))
    metrics.update(notification_metrics(session, scope, f))
    metrics["booking_extraction_accuracy"] = latest_extraction_accuracy(session)

    confirmed = [b for b in bookings if b.status in CONFIRMED and b.total_cents is not None]
    by_building, by_service = defaultdict(float), defaultdict(float)
    for b in confirmed:
        by_building[b.building] += b.total_cents / 100
        by_service[b.service.value] += b.total_cents / 100

    late = {v.id for v in late_check_ins(visits)}
    caregiver_table = []
    for cid, c in sorted(capacity.items(), key=lambda kv: kv[1]["name"]):
        mine = [v for v in visits if v.caregiver_id == cid]
        caregiver_table.append({
            "Caregiver": c["name"], "Available h": round(c["available_minutes"] / 60, 1),
            "Assigned h": round(assigned.get(cid, 0) / 60, 1),
            "Utilization": f"{assigned.get(cid, 0) / c['available_minutes']:.0%}" if c["available_minutes"] else "No hours",
            "Visits (selected buildings)": len(mine),
            "Missed": sum(v.execution == E.MISSED for v in mine),
            "Late check-ins": sum(v.id in late for v in mine),
        })

    building_table = []
    for building in sorted({b.building for b in bookings} | {v.building for v in visits}):
        bb = [b for b in bookings if b.building == building]
        vm = visit_metrics([v for v in visits if v.building == building], as_of)
        building_table.append({
            "Building": building,
            "Bookings": booking_metrics(bb)["total_bookings"].value,
            "Confirmed": booking_metrics(bb)["confirmed_bookings"].value,
            "GBV": f"${by_building.get(building, 0):,.2f}",
            "Visits": vm["total_scheduled_visits"].value,
            "Completed": vm["completed_visits"].value,
            "Missed": vm["missed_visits"].value,
            "Completion": vm["visit_completion_rate"].display(),
        })

    weekly: dict[date, dict[str, float]] = {}
    for b in bookings:
        if b.status == S.DRAFT:
            continue
        week = b.start_date - timedelta(days=b.start_date.weekday())
        w = weekly.setdefault(week, {"bookings": 0, "confirmed": 0, "gbv": 0.0})
        w["bookings"] += 1
        if b.status in CONFIRMED:
            w["confirmed"] += 1
            w["gbv"] += (b.total_cents or 0) / 100
    weekday = Counter(v.visit_date.strftime("%a") for v in visits)

    return KPISnapshot(
        filter=f, scope_buildings=None if scope.buildings is None else sorted(scope.buildings), as_of=as_of,
        metrics=metrics, revenue_by_building=dict(by_building), revenue_by_service=dict(by_service),
        weekly=[{"week": k, **v} for k, v in sorted(weekly.items())],
        building_table=building_table, caregiver_table=caregiver_table,
        weekday_visits={d: weekday.get(d, 0) for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]},
        coverage={
            "data_source": f.data_source, "period": f"{f.start.isoformat()} to {f.end.isoformat()}",
            "days": f.days, "bookings": len(bookings), "requests": len(requests), "visits": len(visits),
            "reports": len(reports), "caregivers": len(capacity),
            "buildings": sorted({b.building for b in bookings} | {v.building for v in visits}),
        },
    )


def report_metrics(session: Session, reports) -> dict[str, Metric]:
    claims = unsupported = ai_claims = ai_unsupported = expected = detected = 0
    delivery: list[float] = []
    for r in reports:
        ev = r.evaluation or {}
        final = ev.get("final") or {}
        claims += final.get("claims_checked", 0)
        unsupported += len(final.get("unsupported_claims", []))
        expected += final.get("expected_missing_items", 0)
        detected += final.get("detected_missing_items", 0)
        cand = ev.get("ai_candidate")
        if isinstance(cand, dict) and "claims_checked" in cand:
            ai_claims += cand["claims_checked"]
            ai_unsupported += len(cand.get("unsupported_claims", []))
        if r.review_status == ReportStatus.APPROVED and r.reviewed_at:
            out = last_checkout(session, r.booking_id, r.report_date)
            h = _hours(out, as_utc(r.reviewed_at))
            if h is not None and h >= 0:
                delivery.append(h)
    corrected = rejected_report_ids(session, [r.id for r in reports])
    reviewed = [r for r in reports if r.review_status != ReportStatus.PENDING_REVIEW or r.id in corrected]
    return {
        "report_unsupported_claim_rate": ratio("report_unsupported_claim_rate", unsupported, claims),
        "ai_draft_unsupported_claim_rate": ratio("ai_draft_unsupported_claim_rate", ai_unsupported, ai_claims,
                                                 "" if ai_claims else "No AI-written drafts in this period (template reports only)."),
        "missing_activity_detection": ratio("missing_activity_detection", detected, expected),
        "avg_report_delivery_hours": average("avg_report_delivery_hours", delivery),
        "human_correction_rate": ratio("human_correction_rate", len(corrected & {r.id for r in reviewed}), len(reviewed)),
    }


def notification_metrics(session: Session, scope: Scope, f: AnalyticsFilter) -> dict[str, Metric]:
    emails = load_email_notifications(session, scope, f)
    sent = sum(1 for n in emails if n.delivery_status.value == "sent")
    failed = sum(1 for n in emails if n.delivery_status.value == "failed")
    pending = len(emails) - sent - failed
    return {"notification_success_rate": ratio("notification_success_rate", sent, sent + failed,
                                               f"{pending} email(s) still pending." if pending else "")}


def latest_extraction_accuracy(session: Session) -> Metric:
    run = session.scalar(select(EvaluationRun).where(EvaluationRun.kind == "booking_extraction")
                         .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc()))
    if run is None:
        return Metric("booking_extraction_accuracy", None, None, None, "no_data", "No benchmark run stored yet.")
    m = run.metrics
    return Metric("booking_extraction_accuracy", m.get("field_accuracy"), m.get("fields_correct"),
                  m.get("fields_evaluated"), "ok", f"System: {run.system}; {run.dataset_size} cases ({run.dataset_version}).")


def definitions_markdown() -> str:
    lines = ["| KPI | Formula | Numerator | Denominator | Time window | Exclusions | Missing data |",
             "|---|---|---|---|---|---|---|"]
    for d in KPI_DEFINITIONS.values():
        lines.append(f"| **{d.label}** | {d.formula} | {d.numerator} | {d.denominator} | {d.window} | {d.exclusions} | {d.missing} |")
    return "\n".join(lines)
