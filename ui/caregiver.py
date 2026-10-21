"""Caregiver Portal: schedule, working hours, time off, skills."""
from __future__ import annotations

from datetime import date, time, timedelta

import streamlit as st

from pawpal.actors import Actor, AuthorizationError
from pawpal.availability import (
    AvailabilityError,
    Block,
    apply_weekly_template,
    blocks_on,
    cancel_time_off,
    caregiver_visits,
    list_time_off,
    mark_day_off,
    request_time_off,
    set_capabilities,
    set_day_availability,
)
from pawpal.database import session_scope
from pawpal.models import Caregiver, ServiceType
from pawpal.schemas import SERVICE_LABELS
from pawpal.workflow import STATUS_LABELS

from . import caregiver_visits as visits_page
from .common import done, show_flash, table

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SPECIES = ["cat", "dog", "rabbit", "bird", "other"]
ERRORS = (AvailabilityError, AuthorizationError)


def _run(action, success: str) -> None:
    try:
        with session_scope() as s:
            action(s)
    except ERRORS as exc:
        st.error(str(exc))
        return
    done(success)


def _blocks_inputs(key: str, defaults=(time(8), time(12), time(16), time(20)), second=True) -> list[Block]:
    c1, c2, c3, c4 = st.columns(4)
    s1 = c1.time_input("Start", value=defaults[0], step=900, key=f"{key}_s1")
    e1 = c2.time_input("End", value=defaults[1], step=900, key=f"{key}_e1")
    blocks = [Block(s1, e1)]
    if second:
        use2 = st.checkbox("Add a second block (split shift)", value=True, key=f"{key}_use2")
        if use2:
            s2 = c3.time_input("Start 2", value=defaults[2], step=900, key=f"{key}_s2")
            e2 = c4.time_input("End 2", value=defaults[3], step=900, key=f"{key}_e2")
            blocks.append(Block(s2, e2))
    return blocks


def schedule_tab(cg: Caregiver) -> None:
    c1, c2 = st.columns([1, 1])
    start = c1.date_input("Week starting", value=date.today(), key="cg_week")
    view = c2.radio("View", ["Week", "Day"], horizontal=True, key="cg_view")
    end = start + timedelta(days=6 if view == "Week" else 0)
    with session_scope() as s:
        visits = caregiver_visits(s, cg.id, start, end)
        rows = [
            {
                "Date": v.visit_date, "Day": f"{v.visit_date:%a}",
                "Time": f"{v.start_time:%H:%M}–{v.end_time:%H:%M}",
                "Building": v.booking.resident.building_name, "Apt": v.booking.resident.apartment_number,
                "Pet": f"{v.booking.pet.name} ({v.booking.pet.species})",
                "Service": SERVICE_LABELS[v.service_type].split(" (")[0],
                "Status": "Confirmed" if v.assignment_status.value == "confirmed" else "Proposed (awaiting approval)",
                "Booking": f"#{v.booking_id} · {STATUS_LABELS[v.booking.status]}",
            }
            for v in visits
        ]
    m1, m2, m3 = st.columns(3)
    m1.metric("Visits", len(rows))
    m2.metric("Confirmed", sum(r["Status"] == "Confirmed" for r in rows))
    m3.metric("Buildings", len({r["Building"] for r in rows}))
    if view == "Week" and rows:
        for d in sorted({r["Date"] for r in rows}):
            st.markdown(f"**{d:%A, %b %d}**")
            table([r for r in rows if r["Date"] == d])
    else:
        table(rows, empty="No visits in this period.")


def availability_tab(cg: Caregiver, actor: Actor) -> None:
    start = st.date_input("Show 14 days from", value=date.today(), key="cg_av_from")
    with session_scope() as s:
        rows = []
        for i in range(14):
            d = start + timedelta(days=i)
            blocks = blocks_on(s, cg.id, d)
            rows.append({"Date": d, "Day": f"{d:%a}", "Hours": ", ".join(b.label() for b in blocks) or "Not working"})
        offs = list_time_off(s, cg.id, start)
    for r in rows:
        if any(t.start_date <= r["Date"] <= t.end_date for t in offs):
            r["Hours"] = "Time off"
    table(rows)

    st.markdown("#### Set hours for one day")
    with st.form("cg_day"):
        day = st.date_input("Date", value=start, key="cg_day_date")
        working = st.checkbox("Working this day", value=True, key="cg_day_working")
        blocks = _blocks_inputs("cg_day")
        if st.form_submit_button("Save day", type="primary"):
            _run(lambda s: set_day_availability(s, cg.id, day, blocks if working else [], actor),
                 f"Hours saved for {day:%b %d}.")

    st.markdown("#### Apply a weekly pattern")
    with st.form("cg_template"):
        c1, c2 = st.columns(2)
        t_start = c1.date_input("From", value=start, key="cg_t_from")
        t_end = c2.date_input("To", value=start + timedelta(days=27), key="cg_t_to")
        days = st.multiselect("Weekdays", list(range(7)), default=[0, 1, 2, 3, 4],
                              format_func=lambda i: WEEKDAYS[i], key="cg_t_days")
        blocks_t = _blocks_inputs("cg_t")
        if st.form_submit_button("Apply pattern", type="primary"):
            def act(s):
                n = apply_weekly_template(s, cg.id, actor, t_start, t_end, days, blocks_t)
                st.session_state["_cg_applied"] = n
            _run(act, "Weekly pattern applied.")

    st.markdown("#### Mark a day off")
    c1, c2, c3 = st.columns([1, 2, 1])
    off_day = c1.date_input("Day", value=start, key="cg_off_day")
    off_reason = c2.text_input("Reason (optional)", key="cg_off_reason")
    c3.write("")
    if c3.button("Mark day off"):
        _run(lambda s: mark_day_off(s, cg.id, actor, off_day, off_reason), f"{off_day:%b %d} marked as a day off.")


def time_off_tab(cg: Caregiver, actor: Actor) -> None:
    with st.form("cg_timeoff"):
        c1, c2 = st.columns(2)
        t_start = c1.date_input("First day off", value=date.today() + timedelta(days=7), key="cg_to_s")
        t_end = c2.date_input("Last day off", value=date.today() + timedelta(days=9), key="cg_to_e")
        reason = st.text_input("Reason (optional)", key="cg_to_reason")
        st.caption("Time off over confirmed visits is blocked until a property manager reassigns them. "
                   "Proposed visits are flagged for the manager automatically.")
        if st.form_submit_button("Request time off", type="primary"):
            _run(lambda s: request_time_off(s, cg.id, actor, t_start, t_end, reason), "Time off recorded.")

    with session_scope() as s:
        items = [(t.id, t.start_date, t.end_date, t.reason) for t in list_time_off(s, cg.id)]
    if not items:
        st.info("No time off recorded.")
        return
    for tid, a, b, reason in items:
        c1, c2 = st.columns([4, 1])
        c1.write(f"**{a:%b %d}** – **{b:%b %d, %Y}** · {reason or 'Time off'}")
        if c2.button("Remove", key=f"rm_to_{tid}"):
            _run(lambda s, tid=tid: cancel_time_off(s, tid, actor), "Time off removed.")


def skills_tab(cg: Caregiver, actor: Actor, buildings: list[str]) -> None:
    with st.form("cg_skills"):
        species = st.multiselect("Pet types", SPECIES, default=sorted(cg.species_set), key="cg_species")
        services = st.multiselect("Services", list(ServiceType), default=sorted(cg.service_set, key=lambda x: x.value),
                                  format_func=lambda x: SERVICE_LABELS[x], key="cg_services")
        bset = st.multiselect("Buildings covered", buildings, default=sorted(cg.building_set & set(buildings)), key="cg_buildings")
        if st.form_submit_button("Save skills", type="primary"):
            _run(lambda s: set_capabilities(s, cg.id, actor, species, services, bset), "Skills updated.")


def render(caregivers: list[Caregiver], buildings: list[str]) -> None:
    show_flash()
    if not caregivers:
        st.info("No caregivers yet.")
        return
    by_id = {c.id: c for c in caregivers}
    cid = st.selectbox("Caregiver", list(by_id),
                       format_func=lambda i: by_id[i].full_name + ("" if by_id[i].active_status else " (inactive)"),
                       key="cg_pick")
    actor = Actor.caregiver(cid)
    with session_scope() as s:
        cg = s.get(Caregiver, cid)
        _ = (cg.species_set, cg.service_set, cg.building_set)  # load while the session is open
    if not cg.active_status:
        st.warning("This caregiver is inactive and will not be matched to new visits.")
    st.caption(
        f"Cares for: {', '.join(sorted(cg.species_set)) or '—'} · "
        f"Services: {', '.join(sorted(SERVICE_LABELS[x].split(' (')[0] for x in cg.service_set)) or '—'} · "
        f"Buildings: {', '.join(sorted(cg.building_set)) or '—'}"
    )
    tab = st.radio("Caregiver portal", ["Today's Visits", "My schedule", "Availability", "Time off", "Skills & buildings"],
                   horizontal=True, label_visibility="collapsed", key="cg_tab")
    if tab == "Today's Visits":
        visits_page.render(cg)
    elif tab == "My schedule":
        schedule_tab(cg)
    elif tab == "Availability":
        availability_tab(cg, actor)
    elif tab == "Time off":
        time_off_tab(cg, actor)
    else:
        skills_tab(cg, actor, buildings)
