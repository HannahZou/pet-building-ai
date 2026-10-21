"""Resident Portal (Week 3): upcoming visits, photos, approved daily reports, notifications.

Every query is scoped to the selected resident; photos are served as bytes after an
authorisation check, so no file paths ever reach the page.
"""
from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy import select

from pawpal.actors import Actor, AuthorizationError
from pawpal.care_logs import ACTIVITY_LABELS
from pawpal.care_reports import reports_for_resident
from pawpal.database import session_scope
from pawpal.models import ActivityType, Booking, BookingStatus, NotificationType as N, Resident, Visit, VisitStatus
from pawpal.notifications import mark_read, resident_notifications
from pawpal.photos import photos_for_resident, read_photo
from pawpal.timeutil import building_tz, default_timezone, fmt_local, local_today
from pawpal.visit_execution import LABELS, get_execution

from .common import done, table

ICONS = {N.VISIT_STARTED: "▶", N.VISIT_COMPLETED: "✓", N.DAILY_REPORT_AVAILABLE: "📄",
         N.MISSED_VISIT: "⚠", N.CARE_ISSUE: "⚠"}


def upcoming_visits(resident: Resident) -> None:
    actor_tz = ZoneInfo(default_timezone())
    show_past = st.toggle("Include the last 7 days", value=True, key="res_vis_past")
    start = local_today(actor_tz) - timedelta(days=7 if show_past else 0)
    with session_scope() as s:
        tz = building_tz(s, resident.building_name)
        visits = s.scalars(
            select(Visit).join(Booking, Visit.booking_id == Booking.id)
            .where(Booking.resident_id == resident.id,
                   Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.COMPLETED]),
                   Visit.assignment_status.in_([VisitStatus.CONFIRMED, VisitStatus.COMPLETED]),
                   Visit.visit_date >= start)
            .order_by(Visit.visit_date, Visit.start_time)
        ).all()
        rows = []
        for v in visits:
            ex = get_execution(s, v.id)
            rows.append({
                "Date": v.visit_date, "Day": f"{v.visit_date:%a}", "Scheduled": f"{v.start_time:%H:%M}–{v.end_time:%H:%M}",
                "Pet": v.booking.pet.name, "Caregiver": v.caregiver.full_name.split()[0] if v.caregiver else "—",
                "Status": LABELS[ex.execution_status] if ex else "Scheduled",
                "Arrived": (fmt_local(ex.actual_check_in, tz) if ex else None) or "—",
                "Left": (fmt_local(ex.actual_check_out, tz) if ex else None) or "—",
            })
    st.caption(f"Times are local to {resident.building_name} ({tz}).")
    table(rows, empty="No confirmed visits in this period.", height=min(520, 38 + 35 * max(1, len(rows))))


def care_photos(resident: Resident) -> None:
    actor = Actor.resident(resident.id)
    with session_scope() as s:
        tz = building_tz(s, resident.building_name)
        items = []
        for p in photos_for_resident(s, actor):
            try:
                items.append((read_photo(s, p.id, actor), p.caption, p.visit.booking.pet.name, fmt_local(p.uploaded_at, tz, True)))
            except AuthorizationError:
                continue
    if not items:
        st.info("No care photos yet. Your caregiver can share photos during visits.")
        return
    st.caption("Photos show a moment from the visit; they are not a record of which care tasks were done.")
    cols = st.columns(3)
    for i, (data, caption, pet, when) in enumerate(items):
        with cols[i % 3]:
            st.image(data, use_container_width=True)
            st.caption(f"**{pet}** · {when}" + (f" — {caption}" if caption else ""))


def daily_reports(resident: Resident) -> None:
    actor = Actor.resident(resident.id)
    with session_scope() as s:
        reports = [(r.id, r.report_date, r.pet.name, r.needs_attention, r.structured_output, r.reviewed_at)
                   for r in reports_for_resident(s, actor)]
    if not reports:
        st.info("No daily care reports yet. Reports appear here after our team has reviewed them.")
        return
    by_id = {r[0]: r for r in reports}
    rid = st.selectbox("Report", list(by_id),
                       format_func=lambda i: f"{by_id[i][2]} · {by_id[i][1]:%a %b %d, %Y}" + (" · needs attention" if by_id[i][3] else ""),
                       key="res_report")
    _, day, pet, attention, out, reviewed = by_id[rid]
    st.markdown(f"### {pet} · {day:%A, %B} {day.day}")
    if reviewed:
        st.caption(f"Reviewed by the PawPal team before publishing. Times are local ({out.get('timezone', '')}).")
    if attention:
        st.warning("Needs your attention:\n\n" + "\n".join(f"- {i}" for i in out["issues_requiring_attention"]))
    for v in out["visit_summaries"]:
        with st.container(border=True):
            st.markdown(f"**Visit {v['visit_ref']}** · scheduled {v['scheduled']} · {v['status']} · "
                        f"arrived {v['check_in']} · left {v['check_out']}")
            st.write(v["summary"])
            done_here = [ACTIVITY_LABELS[ActivityType(a["activity"])] for a in out["completed_activities"] if a["visit_ref"] == v["visit_ref"]]
            missing = [f"{ACTIVITY_LABELS[ActivityType(a['activity'])]} ({a['status'].replace('_', ' ')})"
                       for a in out["incomplete_activities"] if a["visit_ref"] == v["visit_ref"]]
            if done_here:
                st.caption("Completed: " + ", ".join(done_here))
            if missing:
                st.caption("Not completed: " + ", ".join(missing))
    if out["caregiver_observations"]:
        st.markdown("**Caregiver notes**")
        for o in out["caregiver_observations"]:
            st.write(f"- {o['visit_ref']} · {o['activity']}: {o['observation']}")
    if out["photo_references"]:
        with session_scope() as s:
            imgs = []
            for p in out["photo_references"]:
                try:
                    imgs.append((read_photo(s, p["photo_id"], actor), p["caption"]))
                except AuthorizationError:
                    continue  # hidden by a manager
        if imgs:
            cols = st.columns(min(3, len(imgs)))
            for i, (data, caption) in enumerate(imgs):
                cols[i % len(cols)].image(data, caption=caption or None, use_container_width=True)
    st.info(out["overall_summary"])


def notification_center(resident: Resident) -> None:
    actor = Actor.resident(resident.id)
    with session_scope() as s:
        items = [(n.id, n.notification_type, n.message, n.created_at, n.read_at) for n in resident_notifications(s, actor)]
    if not items:
        st.info("No notifications yet.")
        return
    unread = [i for i in items if i[4] is None]
    c1, c2 = st.columns([3, 1])
    c1.caption(f"{len(unread)} unread · {len(items)} total")
    mark_all = c2.button("Mark all as read", disabled=not unread)
    clicked = None
    for nid, ntype, message, created, read_at in items[:50]:
        with st.container(border=True):
            a, b = st.columns([5, 1])
            a.markdown(f"{ICONS.get(ntype, '•')} {'**' if read_at is None else ''}{message}{'**' if read_at is None else ''}")
            a.caption(f"{created:%b %d %H:%M} UTC · {ntype.value.replace('_', ' ')}")
            if read_at is None and b.button("Read", key=f"read_{nid}"):
                clicked = [nid]
    if mark_all:
        clicked = [i[0] for i in unread]
    if clicked:
        try:
            with session_scope() as s:
                for nid in clicked:
                    mark_read(s, nid, actor)
        except AuthorizationError as exc:
            st.error(str(exc))
            return
        done("Marked as read.")


def unread_count(resident: Resident) -> int:
    with session_scope() as s:
        return len(resident_notifications(s, Actor.resident(resident.id), unread_only=True))
