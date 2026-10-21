"""Property Manager Portal (Week 3): visit completion, reports, issues, notifications.

Managers see only their buildings. Pet medical notes are shown only when the
manager's ``can_view_medical_notes`` permission is set.
"""
from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from sqlalchemy import select

from pawpal.actors import Actor, AuthorizationError
from pawpal.care_reports import (
    ReportError, approve_report, generate_daily_report, reject_report, reportable_days, reports_for_manager,
)
from pawpal.database import session_scope
from pawpal.evaluation import compute_metrics
from pawpal.models import (
    Booking, BookingStatus, DailyCareReport, Notification, PetCarePhoto, PhotoVisibility, PropertyManager,
    ReportStatus, Resident,
)
from pawpal.notifications import deliver_pending, delivery_stats, email_provider
from pawpal.photos import read_photo, set_visibility
from pawpal.reports import execution_rows, open_visit_issues
from pawpal.timeutil import default_timezone, local_today
from pawpal.visit_execution import ExecutionError, resolve_review, run_daily_checks

from .common import done, note, table

SECTIONS = ["Visit completion", "Care reports", "Service issues", "Notifications"]
ERRORS = (ReportError, ExecutionError, AuthorizationError)


def _run(action, success: str) -> None:
    try:
        with session_scope() as s:
            action(s)
    except ERRORS as exc:
        st.error(str(exc))
        return
    done(success)


def _end_of_day_checks(manager: PropertyManager) -> str:
    with session_scope() as s:
        result = run_daily_checks(s)
        days = result.completed_days
    drafted = 0
    for booking_id, day in days:
        try:
            with session_scope() as s:
                b = s.get(Booking, booking_id)
                if manager.manages(b.resident.building_name):
                    generate_daily_report(s, booking_id, day)
                    drafted += 1
        except ReportError:
            continue
    with session_scope() as s:
        deliver_pending(s)
    return (f"Checked: {result.missed} visit(s) marked missed, {result.unclosed} without check-out sent to review, "
            f"{drafted} daily report(s) drafted.")


# ------------------------------------------------------------ visit completion
def visit_completion(manager: PropertyManager) -> None:
    today = local_today(ZoneInfo(default_timezone()))
    c1, c2, c3 = st.columns([1, 1, 1.3])
    start = c1.date_input("From", value=today - timedelta(days=10), key="pmv_from")
    end = c2.date_input("To", value=today + timedelta(days=1), key="pmv_to")
    c3.write("")
    run_checks = c3.button("Run end-of-day checks", help="Marks visits from past days with no check-in as Missed and "
                                                         "visits with no check-out as Needs Review. Never marks anything Completed.")
    with session_scope() as s:
        rows = execution_rows(s, start, end, manager.building_name)
    counts = pd.Series([r["Status"] for r in rows]).value_counts().to_dict() if rows else {}
    finished = counts.get("Completed", 0) + counts.get("Needs Review", 0)
    known = finished + counts.get("Missed", 0)
    k = st.columns(6)
    k[0].metric("Visits", len(rows))
    k[1].metric("Completed", counts.get("Completed", 0))
    k[2].metric("In progress", counts.get("In Progress", 0))
    k[3].metric("Needs review", counts.get("Needs Review", 0))
    k[4].metric("Missed", counts.get("Missed", 0))
    k[5].metric("Completion rate", f"{finished / known:.0%}" if known else "—",
                help="Visits checked out ÷ visits whose outcome is known (checked out or missed).")
    overdue = [r for r in rows if r["Status"].startswith("Overdue")]
    if overdue:
        st.warning(f"{len(overdue)} visit(s) are more than an hour past their scheduled end and not started.")
    st.markdown("**Missed visits**")
    table([r for r in rows if r["Status"] == "Missed"], empty="No missed visits in this period.")
    st.markdown("**All visits**")
    table(rows, empty="No confirmed visits in this period.", height=420)
    if run_checks:
        done(_end_of_day_checks(manager))


# ---------------------------------------------------------------- reports
def _report_view(report_id: int, manager: PropertyManager, actor: Actor):
    action = None
    with session_scope() as s:
        r = s.get(DailyCareReport, report_id)
        out, ev = r.structured_output, r.evaluation or {}
        st.markdown(f"#### {r.pet.name} · {r.report_date:%a %b %d, %Y} · booking #{r.booking_id}")
        st.caption(f"Status: **{r.review_status.value.replace('_', ' ')}** · generated {r.generated_at:%b %d %H:%M} UTC · "
                   f"model `{r.model_identifier}` · version {r.generation_count}")
        if r.needs_attention:
            st.warning("Needs attention:\n\n" + "\n".join(f"- {i}" for i in out["issues_requiring_attention"]))
        if r.review_notes:
            st.caption(f"Review notes: {r.review_notes}")
        st.text(r.report_text)
        final, cand = ev.get("final", {}), ev.get("ai_candidate")
        m = st.columns(4)
        m[0].metric("Narrative source", ev.get("narrative_source", "—").replace("_", " "))
        m[1].metric("Factual consistency", f"{final.get('factual_consistency', 0):.0%}" if final else "—")
        m[2].metric("Claims checked", final.get("claims_checked", "—"))
        m[3].metric("Missing-item detection", f"{final.get('missing_activity_detection', 0):.0%}" if final else "—")
        if isinstance(cand, dict) and cand.get("unsupported_claims"):
            st.error("The AI draft made unsupported claims, so its text was not used:\n\n"
                     + "\n".join(f"- {c}" for c in cand["unsupported_claims"]))
        refs = r.source_refs
        st.caption(f"Sources: {len(refs.get('visits', []))} visits, {len(refs.get('activity_logs', []))} activity logs, "
                   f"{len(refs.get('photos', []))} photos.")
        if manager.can_view_medical_notes and r.pet.medical_notes:
            with st.expander("Medical notes (you are authorised to view these)"):
                st.write(r.pet.medical_notes)
        photos = [s.get(PetCarePhoto, p["photo_id"]) for p in out["photo_references"]]
        if photos:
            cols = st.columns(min(4, len(photos)))
            for i, p in enumerate(photos):
                with cols[i % len(cols)]:
                    st.image(read_photo(s, p.id, actor), caption=p.caption or None, width=170)
                    hidden = p.visibility_status == PhotoVisibility.HIDDEN
                    if st.button("Show to resident" if hidden else "Hide from resident", key=f"vis_{p.id}"):
                        action = ("visibility", p.id, PhotoVisibility.VISIBLE if hidden else PhotoVisibility.HIDDEN)
        if r.review_status == ReportStatus.PENDING_REVIEW:
            notes = st.text_input("Review note (required to reject)", key=f"rn_{r.id}")
            c1, c2, c3 = st.columns([1, 1, 1.4])
            if c1.button("Approve & publish", type="primary", key=f"ap_{r.id}"):
                action = ("approve", r.id, notes)
            if c2.button("Reject", key=f"rj_{r.id}"):
                action = ("reject", r.id, notes)
            if c3.button("Regenerate", key=f"rg_{r.id}"):
                action = ("regen", r.booking_id, r.report_date)
        elif r.review_status == ReportStatus.REJECTED:
            if st.button("Regenerate", key=f"rg_{r.id}"):
                action = ("regen", r.booking_id, r.report_date)
    return action


def care_reports(manager: PropertyManager, actor: Actor) -> None:
    status = st.radio("Show", ["Pending review", "Approved", "Rejected", "All"], horizontal=True, key="pmr_status")
    wanted = {"Pending review": ReportStatus.PENDING_REVIEW, "Approved": ReportStatus.APPROVED,
              "Rejected": ReportStatus.REJECTED}.get(status)
    with session_scope() as s:
        reports = [(r.id, r.pet.name, r.report_date, r.review_status.value, r.needs_attention,
                    r.booking.resident.building_name, (r.evaluation or {}).get("narrative_source", "—"))
                   for r in reports_for_manager(s, actor, wanted)]
        # bookings in scope with finished days but no report yet
        missing = []
        for b in s.scalars(select(Booking).join(Resident, Booking.resident_id == Resident.id)
                           .where(Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.COMPLETED]))):
            if not manager.manages(b.resident.building_name):
                continue
            have = {r.report_date for r in s.scalars(select(DailyCareReport).where(DailyCareReport.booking_id == b.id))}
            missing += [(b.id, d, b.pet.name) for d in reportable_days(s, b.id) if d not in have]
    action = None
    if missing:
        with st.expander(f"Days with visit records but no report ({len(missing)})"):
            labels = {f"#{bid} · {pet} · {d:%b %d}": (bid, d) for bid, d, pet in missing}
            pick = st.selectbox("Generate report for", list(labels), key="pmr_gen")
            if st.button("Generate draft report"):
                action = ("regen", *labels[pick])
    table([{"Report": i, "Pet": pet, "Date": d, "Status": st_.replace("_", " "), "Attention": "Yes" if att else "",
            "Building": bld, "Narrative": src.replace("_", " ")} for i, pet, d, st_, att, bld, src in reports],
          empty="No reports in this list.")
    if reports:
        ids = [r[0] for r in reports]
        rid = st.selectbox("Open report", ids, key="pmr_open",
                           format_func=lambda i: next(f"#{r[0]} · {r[1]} · {r[2]:%b %d}" for r in reports if r[0] == i))
        action = _report_view(rid, manager, actor) or action
    if not action:
        return
    if action[0] == "approve":
        _run(lambda s: approve_report(s, action[1], actor, action[2]), "Report approved and published; the resident was notified.")
    elif action[0] == "reject":
        _run(lambda s: reject_report(s, action[1], actor, action[2]), "Report rejected.")
    elif action[0] == "visibility":
        _run(lambda s: set_visibility(s, action[1], actor, action[2]), "Photo visibility updated.")
    else:
        _run(lambda s: generate_daily_report(s, action[1], action[2], actor), "Draft report generated for review.")


# ------------------------------------------------------------- service issues
def service_issues(manager: PropertyManager, actor: Actor) -> None:
    with session_scope() as s:
        issues = open_visit_issues(s, manager.building_name)
        attention = [(r.id, r.pet.name, r.report_date) for r in reports_for_manager(s, actor, ReportStatus.PENDING_REVIEW)
                     if r.needs_attention]
    st.markdown("**Visits needing follow-up**")
    table(issues, empty="No open visit issues.")
    if attention:
        st.markdown("**Reports with issues awaiting review**")
        st.write(", ".join(f"#{i} {pet} ({d:%b %d})" for i, pet, d in attention) + " — open them under *Care reports*.")
    if not issues:
        return
    with st.form("resolve_issue"):
        visit_id = st.selectbox("Resolve visit", [i["Visit"] for i in issues],
                                format_func=lambda v: next(f"{i['Date']:%b %d} {i['Time']} · {i['Pet']} · {i['Status']}" for i in issues if i["Visit"] == v))
        note_text = st.text_area("What was done? (required)", placeholder="e.g. Called the resident; backup caregiver visited at 19:30")
        st.caption("Resolving records your follow-up. It does not change what happened at the visit.")
        if st.form_submit_button("Mark as resolved", type="primary"):
            _run(lambda s: resolve_review(s, visit_id, actor, note_text), "Issue marked as resolved.")


# -------------------------------------------------------------- notifications
def notifications(manager: PropertyManager) -> None:
    provider = email_provider()
    note(f"Email provider: <b>{provider.name}</b>" + (" — no SMTP credentials configured, so emails are simulated and "
                                                      "nothing leaves this computer." if provider.name == "mock" else ""))
    with session_scope() as s:
        stats = delivery_stats(s)
        metrics = compute_metrics(s)
        rows = []
        q = (select(Notification, Resident).join(Resident, Notification.resident_id == Resident.id)
             .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(300))
        for n, r in s.execute(q).all():
            if manager.manages(r.building_name):
                rows.append({"ID": n.id, "Created": n.created_at, "Resident": r.full_name,
                             "Type": n.notification_type.value.replace("_", " "), "Channel": n.channel.value.replace("_", "-"),
                             "Status": n.delivery_status.value, "Attempts": n.attempts,
                             "Provider": n.provider or "—", "Error": n.last_error or ""})
    k = st.columns(4)
    k[0].metric("Notifications", stats["total"])
    k[1].metric("Emails pending", stats["email_pending"])
    k[2].metric("Emails failed", stats["email_failed"])
    rate = metrics["email_notification_success_rate"]
    k[3].metric("Email success rate", f"{rate:.0%}" if rate is not None else "—")
    if st.button("Deliver pending emails", type="primary", disabled=not (stats["email_pending"] or stats["email_failed"])):
        with session_scope() as s:
            res = deliver_pending(s)
        done(f"Delivery run: {res.sent} sent, {res.failed} failed (failed emails are retried up to 3 times).")
    table(rows, empty="No notifications yet.", height=420)


def render(section: str, manager: PropertyManager, actor: Actor) -> None:
    if section == "Visit completion":
        visit_completion(manager)
    elif section == "Care reports":
        care_reports(manager, actor)
    elif section == "Service issues":
        service_issues(manager, actor)
    else:
        notifications(manager)
