"""Caregiver Portal - Today's Visits: check-in/out, checklist, photos, notes."""
from __future__ import annotations

from datetime import timedelta

import streamlit as st

from pawpal.actors import Actor, AuthorizationError
from pawpal.care_logs import ACTIVITY_LABELS, STATUS_LABELS, CareLogError, checklist_for, essential_activities, log_activity, logs_for_visit
from pawpal.care_reports import ReportError, generate_daily_report
from pawpal.database import session_scope, write_locked_session
from pawpal.models import ActivityType as A, Caregiver, CompletionStatus as C, ExecutionStatus as E
from pawpal.notifications import deliver_pending
from pawpal.photo_storage import PhotoValidationError, max_photo_bytes
from pawpal.photos import photos_for_visit, read_photo, upload_photo
from pawpal.schemas import SERVICE_LABELS
from pawpal.timeutil import building_tz, default_timezone, fmt_local, local_today
from pawpal.visit_execution import LABELS, ExecutionError, caregiver_day, check_in, check_out

from .common import done, note, show_flash

CHOICES = ["—", "Completed", "Not completed", "Skipped"]
TO_STATUS = {"Completed": C.COMPLETED, "Not completed": C.NOT_COMPLETED, "Skipped": C.SKIPPED}
ERRORS = (ExecutionError, CareLogError, PhotoValidationError, AuthorizationError, ReportError)


def _after_checkout(booking_id: int, day) -> str:
    """When the last visit of the day is done, draft the daily report for manager review."""
    try:
        with session_scope() as s:
            generate_daily_report(s, booking_id, day)
        msg = " All visits for the day are done - the daily report was drafted for manager review."
    except ReportError:
        msg = ""
    try:
        with session_scope() as s:
            deliver_pending(s)
    except Exception:
        pass  # delivery is retried later from the manager portal
    return msg


def _visit_card(item: dict, actor: Actor) -> tuple | None:
    """Render one visit; return an action tuple to run after rendering."""
    action = None
    status = item["status"]
    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        c1.markdown(f"**{item['time']} · {item['pet']}** ({item['species']}) — {item['building']} {item['apt']}")
        c1.caption(f"{item['service']} · window {item['window']} · booking #{item['booking']}")
        c2.markdown(f"**{LABELS[status]}**")
        if item["check_in"]:
            c2.caption(f"In {item['check_in']}" + (f" · Out {item['check_out']}" if item["check_out"] else ""))
        if item["duration"] is not None:
            c2.caption(f"Actual duration: {item['duration']} min")

        with st.expander("Care instructions"):
            st.write(item["instructions"] or "No special instructions.")
            if item["medical"]:
                st.caption("Medical notes (confidential - for the assigned caregiver only):")
                st.write(item["medical"])

        if status == E.SCHEDULED:
            if item["is_today"]:
                if st.button("▶ Start Visit", key=f"in_{item['id']}", type="primary"):
                    action = ("check_in", item["id"])
            else:
                st.caption("You can start this visit on its scheduled day.")
        elif status == E.IN_PROGRESS:
            action = _checklist(item, actor) or action
            action = _photo_upload(item) or action
            st.caption("Completing the visit records the current time. Unrecorded essential tasks are saved as "
                       "*Not recorded* and sent to a manager for review.")
            if st.button("■ Complete Visit", key=f"out_{item['id']}", type="primary"):
                action = ("check_out", item["id"], item["booking"], item["date"])
        else:
            for log in item["logs"]:
                st.write(f"- {log['label']}: **{log['status']}**" + (f" — {log['notes']}" if log["notes"] else "")
                         + (" 🚩" if log["flagged"] else ""))
            if item["reasons"]:
                st.warning("Needs review: " + "; ".join(item["reasons"]))
        if item["photos"]:
            cols = st.columns(min(4, len(item["photos"])))
            for i, (data, caption) in enumerate(item["photos"]):
                cols[i % len(cols)].image(data, caption=caption or None, width=160)
    return action


def _checklist(item: dict, actor: Actor):
    existing = {log["activity"]: log for log in item["logs"]}
    with st.form(f"checklist_{item['id']}"):
        st.markdown("**Care checklist** — record only what you actually did")
        entries = []
        for activity in item["checklist"]:
            log = existing.get(activity.value)
            label = ACTIVITY_LABELS[activity] + (" *" if activity in item["essentials"] else "")
            c1, c2, c3 = st.columns([1.3, 1.4, 2.3])
            current = log["status"] if log and log["status"] in CHOICES else "—"
            choice = c1.selectbox(label, CHOICES, index=CHOICES.index(current), key=f"cl_{item['id']}_{activity.value}")
            notes = c2.text_input("Notes", value=(log or {}).get("notes") or "", key=f"cn_{item['id']}_{activity.value}",
                                  placeholder="Factual note")
            flag = c3.checkbox("Flag for manager review", value=bool(log and log["flagged"]),
                               key=f"cf_{item['id']}_{activity.value}")
            entries.append((activity, choice, notes, flag))
        other = st.text_input("Other observation (optional, factual)", key=f"co_{item['id']}")
        other_flag = st.checkbox("Flag this observation", key=f"cof_{item['id']}")
        st.caption("* essential for this booking")
        if st.form_submit_button("Save checklist"):
            if other.strip():
                entries.append((A.OTHER, "Completed", other, other_flag))
            return ("log", item["id"], [(a, TO_STATUS[c], n, f) for a, c, n, f in entries if c in TO_STATUS])
    return None


def _photo_upload(item: dict):
    with st.form(f"photo_{item['id']}", clear_on_submit=True):
        upload = st.file_uploader(f"Add a photo (JPG/PNG, up to {max_photo_bytes() // (1024 * 1024)} MB)",
                                  type=["jpg", "jpeg", "png"], key=f"up_{item['id']}")
        caption = st.text_input("Caption (optional)", key=f"cap_{item['id']}", max_chars=300)
        if st.form_submit_button("Upload photo") and upload is not None:
            return ("photo", item["id"], upload.name, upload.getvalue(), caption)
    return None


def render(cg: Caregiver) -> None:
    show_flash()
    actor = Actor.caregiver(cg.id)
    note("<b>Demo mode:</b> you are acting as the caregiver chosen above. In production the caregiver is the "
         "signed-in user (authentication is not part of this MVP); the server still checks that only the "
         "assigned caregiver can start, update or complete a visit.")
    from zoneinfo import ZoneInfo

    today = local_today(ZoneInfo(default_timezone()))
    day = st.date_input("Day", value=today, key="cg_visit_day")
    items = []
    with session_scope() as s:
        for v, ex in caregiver_day(s, cg.id, day):
            b, r = v.booking, v.booking.resident
            tz = building_tz(s, r.building_name)
            status = ex.execution_status if ex else E.SCHEDULED
            logs = [{"activity": l.activity_type.value, "label": ACTIVITY_LABELS[l.activity_type],
                     "status": STATUS_LABELS[l.completion_status], "notes": l.notes, "flagged": l.flagged}
                    for l in logs_for_visit(s, v.id)]
            photos = []
            for p in photos_for_visit(s, v.id, actor):
                try:
                    photos.append((read_photo(s, p.id, actor), p.caption))
                except Exception:
                    pass
            instructions = " · ".join(x for x in [b.pet.feeding_instructions, b.special_instructions] if x)
            items.append({
                "id": v.id, "booking": b.id, "date": v.visit_date, "time": f"{v.start_time:%H:%M}",
                "window": f"{v.window_start:%H:%M}–{v.window_end:%H:%M}", "pet": b.pet.name, "species": b.pet.species,
                "building": r.building_name, "apt": r.apartment_number,
                "service": SERVICE_LABELS[v.service_type].split(" (")[0], "status": status,
                "check_in": fmt_local(ex.actual_check_in, tz) if ex else None,
                "check_out": fmt_local(ex.actual_check_out, tz) if ex else None,
                "duration": ex.duration_minutes if ex else None, "reasons": (ex.review_reasons if ex else []) or [],
                "logs": logs, "photos": photos, "checklist": checklist_for(v),
                "essentials": essential_activities(v.service_type, b.pet.species),
                "instructions": instructions, "medical": b.pet.medical_notes,
                "is_today": local_today(tz) == v.visit_date,
            })
    if not items:
        st.info(f"No confirmed visits assigned to you on {day:%A, %b %d}.")
        nxt = day + timedelta(days=1)
        st.caption(f"Tip: pick another day above, e.g. {nxt:%b %d}.")
        return
    m1, m2, m3 = st.columns(3)
    m1.metric("Visits", len(items))
    m2.metric("Completed", sum(i["status"] in (E.COMPLETED, E.NEEDS_REVIEW) for i in items))
    m3.metric("In progress", sum(i["status"] == E.IN_PROGRESS for i in items))

    action = None
    for item in items:
        action = _visit_card(item, actor) or action
    if not action:
        return

    try:
        if action[0] == "check_in":
            with write_locked_session() as s:
                check_in(s, action[1], actor)
            msg = "Visit started. The resident has been notified."
        elif action[0] == "check_out":
            with write_locked_session() as s:
                res = check_out(s, action[1], actor)
                needs, reasons, day_done = res.needs_review, res.reasons, res.day_complete
            msg = "Visit completed." + (" Sent for manager review: " + "; ".join(reasons) if needs else "")
            if day_done:
                msg += _after_checkout(action[2], action[3])
        elif action[0] == "log":
            if not action[2]:
                st.warning("Choose a status for at least one activity.")
                return
            with session_scope() as s:
                for activity, status, notes, flag in action[2]:
                    log_activity(s, action[1], actor, activity, status, notes, flag)
            msg = "Checklist saved."
        else:
            _, vid, name, data, caption = action
            with session_scope() as s:
                upload_photo(s, vid, actor, name, data, caption)
            msg = "Photo uploaded (location and camera metadata removed)."
    except ERRORS as exc:
        st.error(str(exc))
        return
    try:
        with session_scope() as s:
            deliver_pending(s)
    except Exception:
        pass
    done(msg)
