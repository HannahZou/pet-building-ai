"""Property Manager Portal: review, conflicts, reassignment, approval."""
from __future__ import annotations

import streamlit as st

from pawpal.actors import Actor, AuthorizationError
from pawpal.database import session_scope
from pawpal.models import Booking, BookingStatus as S, PropertyManager
from pawpal.pricing import PricingError, from_cents, quote_rows
from pawpal.scheduling import SchedulingError, check_booking_schedule, recommend_schedule, visit_rows
from pawpal.workflow import (
    STATUS_LABELS,
    WorkflowError,
    admin_reject,
    booking_rows,
    confirm_booking,
    mark_completed,
    reassign_visits,
)

from .common import done, note, progress, show_badge, show_flash, table

QUEUE = (S.PENDING_ADMIN_APPROVAL, S.NEEDS_RESCHEDULING, S.PENDING_RESIDENT_APPROVAL, S.QUOTED, S.CONFIRMED)
ERRORS = (WorkflowError, SchedulingError, PricingError, AuthorizationError)


def _run(action, success: str) -> None:
    try:
        with session_scope() as s:
            action(s)
    except ERRORS as exc:
        st.error(str(exc))
        return
    done(success)


def render(managers: list[PropertyManager]) -> None:
    show_flash()
    if not managers:
        st.info("No property managers yet. Load the demo data first.")
        return
    by_id = {m.id: m for m in managers}
    mid = st.selectbox("Property manager", list(by_id),
                       format_func=lambda i: f"{by_id[i].full_name} · {by_id[i].building_name or 'All buildings'}",
                       key="pm_pick")
    manager = by_id[mid]
    actor = Actor.manager(mid)

    with session_scope() as s:
        rows = booking_rows(s, statuses=QUEUE, building=manager.building_name)
    counts = {st_: sum(r["Status"] == STATUS_LABELS[st_] for r in rows) for st_ in QUEUE}
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Awaiting your approval", counts[S.PENDING_ADMIN_APPROVAL])
    m2.metric("Needs rescheduling", counts[S.NEEDS_RESCHEDULING])
    m3.metric("Awaiting resident", counts[S.PENDING_RESIDENT_APPROVAL] + counts[S.QUOTED])
    m4.metric("Confirmed", counts[S.CONFIRMED])

    status_filter = st.multiselect("Show", list(QUEUE), default=[S.PENDING_ADMIN_APPROVAL, S.NEEDS_RESCHEDULING],
                                   format_func=lambda x: STATUS_LABELS[x], key="pm_filter")
    shown = [r for r in rows if r["Status"] in {STATUS_LABELS[x] for x in status_filter}]
    table(shown, empty="Nothing in this queue.")
    if not shown:
        return

    ids = [r["ID"] for r in shown]
    bid = st.selectbox("Review booking", ids,
                       format_func=lambda i: next(f"#{r['ID']} · {r['Pet']} · {r['Building']} · {r['Dates']}" for r in shown if r["ID"] == i),
                       key="pm_booking")

    action = None
    with session_scope() as s:
        b = s.get(Booking, bid)
        show_badge(b.status, prefix=f"**#{b.id} · {b.resident.full_name}, {b.resident.building_name} {b.resident.apartment_number}"
                                     f" · {b.pet.name} ({b.pet.species})** &nbsp; ")
        progress(b.status)
        if b.resident_approved_at:
            st.caption(f"Resident approved {b.resident_approved_at:%b %d %H:%M} UTC")
        if b.admin_approved_at:
            st.caption(f"Manager #{b.admin_approved_by} approved {b.admin_approved_at:%b %d %H:%M} UTC")

        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown("**Visits & proposed caregivers**")
            table(visit_rows(b.visits), empty="No visits generated yet.", height=min(420, 38 + 35 * max(1, len(b.visits))))
        with c2:
            q = b.current_quote
            if q:
                st.metric("Quoted total", f"${from_cents(q.total_cents):,.2f}")
                with st.expander("Breakdown"):
                    table(quote_rows(q))

        active = [v for v in b.visits if v.assignment_status.value != "cancelled"]
        if active and b.status not in (S.CONFIRMED, S.COMPLETED, S.CANCELLED, S.REJECTED):
            conflicts = check_booking_schedule(s, b, include_soft=True)
            st.markdown("**Conflict check**")
            if conflicts:
                table([{"Date": c.day, "Time": f"{c.start:%H:%M}–{c.end:%H:%M}", "Problem": c.kind.replace("_", " "),
                        "Severity": "Blocking" if c.hard else "Warning", "Details": c.message} for c in conflicts])
            else:
                st.success("No conflicts: every visit has a qualified, available caregiver.")

            rec = recommend_schedule(s, b)
            with st.expander("Recommended caregivers (ranked)", expanded=b.status == S.NEEDS_RESCHEDULING):
                table([{
                    "Rank": str(c.rank) if c.rank else "—", "Caregiver": c.name, "Eligible": "Yes" if c.eligible else "No",
                    "Can cover": f"{c.coverable}/{len(rec.proposals)}" if c.eligible else "—",
                    "Visits in building": c.same_building_visits,
                    "Notes": "; ".join(c.reasons) or "Available",
                } for c in rec.candidates])
                st.caption("Ranking: most visits covered → most visits already in this building (less travel) → lightest load.")

            if b.status in (S.PENDING_ADMIN_APPROVAL, S.NEEDS_RESCHEDULING, S.PENDING_RESIDENT_APPROVAL):
                with st.expander("Manually reassign visits"):
                    eligible = [c for c in rec.candidates if c.eligible]
                    visit_ids = st.multiselect(
                        "Visits", [v.id for v in b.visits],
                        format_func=lambda vid: next(f"{v.visit_date:%a %b %d} · visit {v.slot} · {v.start_time:%H:%M}"
                                                     for v in b.visits if v.id == vid),
                        key=f"pm_visits_{b.id}")
                    cg_names = {c.caregiver_id: c.name for c in eligible}
                    target = st.selectbox("Assign to", list(cg_names), format_func=lambda i: cg_names[i],
                                          index=None, placeholder="Choose a qualified caregiver", key=f"pm_target_{b.id}")
                    st.caption("Each visit is re-checked against hours, time off and confirmed visits before it is saved.")
                    if st.button("Reassign", disabled=not (visit_ids and target), key=f"pm_reassign_{b.id}"):
                        action = ("reassign", visit_ids, target)

        if b.status == S.PENDING_ADMIN_APPROVAL:
            note("Approving runs a final availability and conflict check inside a locked transaction. "
                 "If anything changed, the booking moves to <b>Needs Rescheduling</b> instead.")
            c1, c2, _ = st.columns([1.3, 1, 3])
            if c1.button("Approve & confirm", type="primary", key=f"pm_ok_{b.id}"):
                action = ("confirm",)
            if c2.button("Reject", key=f"pm_no_{b.id}"):
                action = ("reject",)
        elif b.status == S.NEEDS_RESCHEDULING:
            st.warning("Reassign the unassigned visits above, or ask the resident to accept an alternative time.")
            if st.button("Reject booking", key=f"pm_no_{b.id}"):
                action = ("reject",)
        elif b.status == S.CONFIRMED:
            if st.button("Mark completed", key=f"pm_done_{b.id}"):
                action = ("complete",)

        with st.expander("Activity"):
            table([{"When": e.created_at, "From": e.from_status or "", "To": e.to_status,
                    "By": f"{e.actor_role} #{e.actor_id}" if e.actor_id else e.actor_role, "Note": e.note or ""}
                   for e in b.events], empty="No activity yet.")

    if not action:
        return
    if action[0] == "reassign":
        _run(lambda s: reassign_visits(s, bid, action[1], action[2], actor), "Visits reassigned.")
    elif action[0] == "reject":
        _run(lambda s: admin_reject(s, bid, actor), "Booking rejected.")
    elif action[0] == "complete":
        _run(lambda s: mark_completed(s, bid, actor), "Booking marked completed.")
    elif action[0] == "confirm":
        try:
            result = confirm_booking(bid, actor)
        except ERRORS as exc:
            st.error(str(exc))
            return
        if result.ok:
            done("Booking confirmed. Caregivers' time is now held.")
        else:
            done(result.message + " " + "; ".join(c.message for c in result.conflicts[:3]), kind="warning")
