"""Operational monitoring: alerts for property managers.

Alerts are *read-only*: they describe a problem and suggest a human action.
Nothing here cancels bookings, changes caregiver assignments or makes any other
decision - those stay with authorised people in the Week 2-3 workflows.

Thresholds are configurable: ``AlertThresholds`` defaults, overridable with a JSON
file at ``PAWPAL_ALERT_THRESHOLDS`` (same keys).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access import analytics_scope
from ..actors import Actor
from ..models import (
    Booking, BookingStatus as S, DailyCareReport, ExecutionStatus as E, ReportStatus, Resident, Visit, VisitStatus,
)
from ..timeutil import as_utc
from .data import AnalyticsFilter, load_bookings, load_email_notifications, load_visits
from .kpis import late_check_ins, ratio


@dataclass(frozen=True)
class AlertThresholds:
    conflict_rate_warning: float = 0.20          # share of scheduling attempts with conflicts
    unassigned_lookahead_days: int = 14          # unassigned visits starting within N days
    missed_visits_warning: int = 1               # missed visits in the period
    late_checkin_minutes: int = 30               # check-in this long after scheduled start
    late_checkins_warning: int = 3
    failed_notifications_warning: int = 1
    report_pending_hours: int = 24               # approval backlog age
    confirmation_delay_hours: int = 48           # booking waiting for an approval
    reassignment_warning: int = 2                # manual reassignments on one booking

    @classmethod
    def load(cls) -> "AlertThresholds":
        path = os.getenv("PAWPAL_ALERT_THRESHOLDS")
        if not path:
            return cls()
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class Alert:
    severity: str        # critical | warning | info
    category: str
    title: str
    detail: str
    count: int
    action: str          # suggested human action - never executed automatically
    refs: list[int] = field(default_factory=list)


def _hours_since(ts: datetime | None, now: datetime) -> float:
    return (now - as_utc(ts)).total_seconds() / 3600 if ts else 0.0


def operational_alerts(
    session: Session, actor: Actor, f: AnalyticsFilter, thresholds: AlertThresholds | None = None,
    now: datetime | None = None,
) -> list[Alert]:
    t = thresholds or AlertThresholds.load()
    now = as_utc(now) or datetime.now(timezone.utc)
    scope = analytics_scope(session, actor, f.buildings)
    alerts: list[Alert] = []

    requests = load_bookings(session, scope, f, "created")
    attempts = sum(b.scheduling_attempts for b in requests)
    conflicts = sum(b.scheduling_conflicts for b in requests)
    rate = ratio("scheduling_conflict_rate", conflicts, attempts)
    if rate.value is not None and rate.value >= t.conflict_rate_warning:
        alerts.append(Alert("warning", "Scheduling conflicts", f"Conflict rate {rate.value:.0%}",
                            f"{conflicts} of {attempts} scheduling attempts ended in Needs Rescheduling.", conflicts,
                            "Review caregiver availability for the busiest days.",
                            [b.id for b in requests if b.scheduling_conflicts]))

    # Current-state alerts use the scope but not the date window.
    open_filter = AnalyticsFilter(date.min, date.max, f.buildings, f.service_types, None, f.data_source)
    open_bookings = load_bookings(session, scope, open_filter, "start")
    resched = [b for b in open_bookings if b.status == S.NEEDS_RESCHEDULING]
    if resched:
        alerts.append(Alert("critical", "Unassigned visits", f"{len(resched)} booking(s) need rescheduling",
                            "Some visits have no available caregiver.", len(resched),
                            "Reassign visits or contact the resident (Booking approvals).", [b.id for b in resched]))

    horizon = (now + timedelta(days=t.unassigned_lookahead_days)).date()
    soon = load_visits(session, scope, AnalyticsFilter(now.date(), horizon, f.buildings, f.service_types, None, f.data_source))
    unassigned = [v for v in soon if v.caregiver_id is None]
    if unassigned:
        alerts.append(Alert("critical", "Unassigned visits", f"{len(unassigned)} upcoming visit(s) without a caregiver",
                            f"Within the next {t.unassigned_lookahead_days} days.", len(unassigned),
                            "Assign a caregiver.", [v.id for v in unassigned]))

    visits = load_visits(session, scope, f)
    missed = [v for v in visits if v.execution == E.MISSED]
    if len(missed) >= t.missed_visits_warning:
        alerts.append(Alert("critical", "Missed visits", f"{len(missed)} missed visit(s)",
                            "No check-in was recorded.", len(missed),
                            "Confirm the pet's welfare with the resident and resolve under Service issues.",
                            [v.id for v in missed]))

    late = late_check_ins(visits, t.late_checkin_minutes)
    if len(late) >= t.late_checkins_warning:
        alerts.append(Alert("warning", "Late check-ins", f"{len(late)} check-in(s) over {t.late_checkin_minutes} min late",
                            "Compared with the scheduled start time.", len(late),
                            "Review travel buffers and caregiver workload.", [v.id for v in late]))

    emails = load_email_notifications(session, scope, f)
    failed = [n for n in emails if n.delivery_status.value == "failed"]
    if len(failed) >= t.failed_notifications_warning:
        alerts.append(Alert("warning", "Failed notifications", f"{len(failed)} email notification(s) failed",
                            "Delivery is retried up to 3 times.", len(failed),
                            "Check email settings, then retry delivery (Notifications).", [n.id for n in failed]))

    pending_reports = [r for r in session.scalars(
        select(DailyCareReport).join(Booking, DailyCareReport.booking_id == Booking.id)
        .join(Resident, Booking.resident_id == Resident.id)
        .where(DailyCareReport.review_status == ReportStatus.PENDING_REVIEW,
               Resident.is_synthetic.is_(f.data_source == "synthetic")))
        if scope.allows(r.booking.resident.building_name) and _hours_since(r.generated_at, now) >= t.report_pending_hours]
    if pending_reports:
        alerts.append(Alert("warning", "Reports pending approval",
                            f"{len(pending_reports)} report(s) waiting over {t.report_pending_hours} h",
                            "Residents only see reports after approval.", len(pending_reports),
                            "Review them under Care reports.", [r.id for r in pending_reports]))

    waiting = [b for b in open_bookings if b.status in (S.PENDING_ADMIN_APPROVAL, S.PENDING_RESIDENT_APPROVAL)
               and _hours_since(b.assigned_at or b.created_at, now) >= t.confirmation_delay_hours]
    if waiting:
        alerts.append(Alert("warning", "Confirmation delays",
                            f"{len(waiting)} booking(s) awaiting approval over {t.confirmation_delay_hours} h",
                            "Long waits risk losing the booking.", len(waiting),
                            "Follow up with the resident or approve under Booking approvals.", [b.id for b in waiting]))

    reassigned = [b for b in open_bookings if b.reassignments >= t.reassignment_warning]
    if reassigned:
        alerts.append(Alert("info", "Repeated reassignment",
                            f"{len(reassigned)} booking(s) reassigned {t.reassignment_warning}+ times",
                            "Frequent changes can affect continuity of care.", len(reassigned),
                            "Check whether one caregiver can cover the whole stay.", [b.id for b in reassigned]))

    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(alerts, key=lambda a: (order[a.severity], a.category))
