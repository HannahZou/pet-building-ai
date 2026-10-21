"""Resident Portal: AI booking request (Week 1), quote & schedule review, approval."""
from __future__ import annotations

from datetime import time

import pandas as pd
import streamlit as st

from pawpal.actors import Actor, AuthorizationError
from pawpal.ai_explain import explain_recommendation
from pawpal.ai_extraction import ExtractionError, extract_booking
from pawpal.booking_service import (
    BookingError,
    create_booking_draft,
    draft_from_extraction,
    list_booking_rows,
    list_pets_for_resident,
    review_extraction,
    validate_draft,
)
from pawpal.database import session_scope
from pawpal.models import Booking, BookingStatus as S, Resident, ServiceType
from pawpal.pricing import PricingError, active_price_book, from_cents, quote_rows
from pawpal.schemas import SERVICE_LABELS, BookingDraft
from pawpal.scheduling import (
    PRESET_WINDOWS,
    MissingTimeWindowsError,
    SchedulingError,
    Window,
    booking_windows,
    recommend_schedule,
    set_time_windows,
    visit_rows,
    windows_from_extracted,
)
from pawpal.workflow import (
    PREPARABLE,
    STATUS_LABELS,
    TERMINAL,
    WorkflowError,
    accept_alternative,
    booking_rows,
    cancel_booking,
    prepare_booking,
    resident_approve,
    resident_reject,
)

from . import resident_care
from .common import done, note, progress, show_badge, show_flash, table

EXAMPLE = (
    "I'm traveling from November 6 to November 9, 2026. My cat Luna needs feeding "
    "twice a day and I'd like a photo after every visit."
)
PHOTO_OPTIONS = ["Not specified", "Yes", "No"]
CUSTOM = "Custom time…"
ERRORS = (WorkflowError, SchedulingError, PricingError, AuthorizationError, BookingError)


def _run(action, success: str) -> None:
    """Run a write in its own transaction, then rerun with a success message."""
    try:
        with session_scope() as s:
            action(s)
    except MissingTimeWindowsError as exc:
        st.warning(str(exc))
        return
    except ERRORS as exc:
        st.error(str(exc))
        return
    done(success)


# ======================================================== new booking (Week 1)
def _reset_draft() -> None:
    for key in ("draft", "ai_questions", "pet_mismatch", "ai_windows", "ai_duration"):
        st.session_state.pop(key, None)


def _new_draft(draft: BookingDraft, questions=None, mismatch=None, windows=None, duration=None) -> None:
    st.session_state["draft"] = draft
    st.session_state["draft_id"] = st.session_state.get("draft_id", 0) + 1  # fresh widget keys
    st.session_state["ai_questions"] = questions or []
    st.session_state["pet_mismatch"] = mismatch
    st.session_state["ai_windows"] = windows or []
    st.session_state["ai_duration"] = duration


def new_booking_page(resident: Resident) -> None:
    with session_scope() as s:
        pets = list_pets_for_resident(s, resident.id)

    st.subheader("Request pet care")
    if not pets:
        st.warning("This resident has no pets on file.")
        return

    _pets_by_id = {p.id: p for p in pets}
    pet = _pets_by_id[
        st.selectbox(
            "Pet",
            list(_pets_by_id),
            format_func=lambda pid: f"{_pets_by_id[pid].name} ({_pets_by_id[pid].species})",
        )
    ]

    owner_key = (resident.id, pet.id)
    if st.session_state.get("draft_owner") != owner_key:
        _reset_draft()
        st.session_state["draft_owner"] = owner_key

    request_text = st.text_area("Tell us about your trip", height=130, placeholder=EXAMPLE, key="request_text")
    c1, c2, _ = st.columns([1, 1, 3])
    generate = c1.button("Generate Booking", type="primary")
    manual = c2.button("Fill in manually")

    if generate:
        try:
            with st.spinner("Reading your request..."):
                extracted = extract_booking(request_text, pet_names=[p.name for p in pets])
        except ExtractionError as exc:
            st.error(str(exc))
        else:
            review = review_extraction(extracted, pet.name)
            mismatch = review.errors[0] if review.errors and "selected pet" in review.errors[0] else None
            duration = extracted.visit_duration_minutes if extracted.visit_duration_minutes in (30, 60) else 30
            windows, time_questions = windows_from_extracted(
                extracted.time_windows, extracted.visits_per_day, duration
            )
            questions = list(extracted.clarification_questions)
            if not windows:
                questions += [q for q in time_questions if q not in questions]
            _new_draft(draft_from_extraction(extracted), questions, mismatch, windows, duration)
    elif manual:
        _new_draft(BookingDraft())

    draft: BookingDraft | None = st.session_state.get("draft")
    if draft is None:
        return

    n = st.session_state["draft_id"]
    st.markdown("### Review booking details")
    st.caption("Edit anything below before saving. Nothing is final until you save the draft.")

    questions = st.session_state.get("ai_questions") or []
    if questions:
        st.info("The assistant needs a little more information:\n\n" + "\n".join(f"- {q}" for q in questions))

    col1, col2 = st.columns(2)
    start = col1.date_input("Start date", value=draft.start_date, key=f"start_{n}")
    end = col2.date_input("End date", value=draft.end_date, key=f"end_{n}")
    visits = col1.number_input(
        "Visits per day", min_value=1, max_value=6, step=1, value=draft.visits_per_day, key=f"visits_{n}"
    )
    options = list(ServiceType)
    service = col2.selectbox(
        "Service type",
        options,
        index=options.index(draft.service_type) if draft.service_type else None,
        format_func=lambda x: SERVICE_LABELS[x],
        placeholder="Choose a service",
        key=f"service_{n}",
    )
    photo_default = {True: "Yes", False: "No", None: "Not specified"}[draft.photo_updates_required]
    photo = st.selectbox(
        "Photo updates after visits", PHOTO_OPTIONS, index=PHOTO_OPTIONS.index(photo_default), key=f"photo_{n}"
    )
    notes = st.text_area("Special instructions", value=draft.special_instructions or "", key=f"notes_{n}")

    ai_windows: list[Window] = st.session_state.get("ai_windows") or []
    if ai_windows:
        st.caption("Visit times you mentioned: " + ", ".join(w.label() for w in ai_windows)
                   + " — you can change them in the next step.")

    current = BookingDraft(
        start_date=start,
        end_date=end,
        visits_per_day=int(visits) if visits is not None else None,
        service_type=service,
        special_instructions=notes,
        photo_updates_required={"Yes": True, "No": False}.get(photo),
    )
    result = validate_draft(current)
    errors = list(result.errors)
    if st.session_state.get("pet_mismatch"):
        errors.insert(0, st.session_state["pet_mismatch"])
    for msg in errors:
        st.error(msg)
    for msg in result.warnings:
        st.warning(msg)
    if not errors:
        st.success("All required details are present.")

    note("Saving creates a <b>draft</b>. Next you choose visit time windows, then see your itemised quote "
         "and proposed caregivers in <b>My Bookings</b>. Nothing is confirmed until you and a property "
         "manager both approve.")
    if st.button("Save Booking Draft", type="primary", disabled=bool(errors)):
        try:
            with session_scope() as s:
                booking = create_booking_draft(s, resident.id, pet.id, current)
                booking_id = booking.id
                windows = ai_windows if len(ai_windows) == current.visits_per_day else []
                if windows:
                    booking.visit_duration_minutes = st.session_state.get("ai_duration") or 30
                    try:
                        set_time_windows(s, booking, windows)
                    except SchedulingError:
                        pass  # resident will choose windows in the next step
        except BookingError as exc:
            st.error(str(exc))
        else:
            _reset_draft()
            st.session_state["selected_booking"] = booking_id
            st.session_state["_goto_resident_tab"] = "My Bookings"
            done(f"Booking draft #{booking_id} saved as DRAFT. Open **My Bookings** to choose time windows "
                 "and get your quote.")


# ================================================================= my bookings
def _window_inputs(booking: Booking, key: str) -> list[Window | None]:
    existing = booking_windows(booking)
    labels = list(PRESET_WINDOWS) + [CUSTOM]
    out: list[Window | None] = []
    cols = st.columns(min(booking.visits_per_day, 3))
    for slot in range(1, booking.visits_per_day + 1):
        col = cols[(slot - 1) % len(cols)]
        current = existing[slot - 1] if len(existing) >= slot else None
        idx = None
        if current:
            match = [k for k, v in PRESET_WINDOWS.items() if v == (current.start, current.end)]
            idx = labels.index(match[0]) if match else labels.index(CUSTOM)
        choice = col.selectbox(f"Visit {slot} window", labels, index=idx, placeholder="Choose a time window",
                               key=f"{key}_w{slot}")
        if choice == CUSTOM:
            s_val = col.time_input("From", value=current.start if current else time(9), step=900, key=f"{key}_s{slot}")
            e_val = col.time_input("To", value=current.end if current else time(10), step=900, key=f"{key}_e{slot}")
            out.append(Window(s_val, e_val))
        elif choice:
            out.append(Window(*PRESET_WINDOWS[choice]))
        else:
            out.append(None)
    return out


def _preferences_form(booking: Booking, actor: Actor):
    """Render the form; return a write action to run *after* the read session closes."""
    book = active_price_book()
    key = f"prefs_{booking.id}"
    with st.form(key):
        st.markdown("**Visit preferences**")
        if not booking.time_windows:
            st.caption("Please choose a time window for each daily visit. We never guess visit times.")
        c1, c2 = st.columns(2)
        duration = c1.radio("Visit length", [30, 60], index=[30, 60].index(booking.visit_duration_minutes),
                            format_func=lambda m: f"{m} minutes" + (" (+$15/visit)" if m == 60 else ""),
                            horizontal=True, key=f"{key}_dur")
        addons = c2.multiselect(
            f"Additional services (+${book.additional_service_fee:,.0f} each per visit)",
            list(book.additional_services), default=[a for a in booking.additional_services if a in book.additional_services],
            format_func=lambda c: book.additional_services[c], key=f"{key}_addons",
        )
        windows = _window_inputs(booking, key)
        submitted = st.form_submit_button(
            "Get quote & schedule" if booking.status == S.DRAFT else "Recalculate quote & schedule", type="primary"
        )
    if not submitted:
        return None
    if any(w is None for w in windows):
        st.warning("Please choose a time window for every visit.")
        return None
    booking_id = booking.id
    return lambda s: prepare_booking(s, booking_id, actor, windows=windows, duration_minutes=duration,
                                     additional_services=addons)


def _quote_block(booking: Booking) -> None:
    quote = booking.current_quote
    if not quote:
        return
    st.markdown("**Itemised quote**")
    c1, c2 = st.columns([3, 1])
    with c1:
        table(quote_rows(quote))
    c2.metric("Total", f"${from_cents(quote.total_cents):,.2f}")
    c2.caption(f"Price book {quote.pricing_version}. Prices come only from published rates.")


def _alternatives_block(session, booking: Booking, actor: Actor) -> None:
    unassigned = [v for v in booking.visits if v.caregiver_id is None]
    if not unassigned:
        return
    st.warning(f"{len(unassigned)} visit(s) have no available caregiver in your windows.")
    rec = recommend_schedule(session, booking)
    for v in unassigned:
        alts = rec.alternatives.get(v.id, [])
        st.markdown(f"**{v.visit_date:%a %b %d} · visit {v.slot}** (requested {v.window_start:%H:%M}–{v.window_end:%H:%M})")
        if not alts:
            st.caption("No nearby times are free that day. Try different windows or dates above.")
            continue
        for a in alts:
            c1, c2 = st.columns([4, 1])
            shift = f"{a.minutes_outside_window} min outside your window" if a.minutes_outside_window else "inside your window"
            c1.write(f"{a.caregiver_name.split()[0]} at {a.start:%H:%M}–{a.end:%H:%M} ({shift})")
            if c2.button("Accept", key=f"alt_{v.id}_{a.caregiver_id}_{a.start}"):
                st.session_state["_pending_action"] = ("alt", booking.id, v.id, a.caregiver_id, a.start)


def my_bookings_page(resident: Resident) -> None:
    actor = Actor.resident(resident.id)
    show_closed = st.toggle("Show closed bookings", value=False)
    statuses = None if show_closed else [s for s in S if s not in TERMINAL]
    with session_scope() as s:
        rows = booking_rows(s, statuses=statuses, resident_id=resident.id)
    if not rows:
        st.info("No open bookings. Create one under **New Request**.")
        return
    ids = [r["ID"] for r in rows]
    by_id = {r["ID"]: r for r in rows}
    preferred = st.session_state.get("selected_booking")
    choice = st.selectbox(
        "Booking", ids, index=ids.index(preferred) if preferred in ids else 0,
        format_func=lambda i: f"#{i} · {by_id[i]['Pet']} · {by_id[i]['Dates']} · {by_id[i]['Status']}",
    )
    st.session_state["selected_booking"] = choice

    action = None
    with session_scope() as s:
        b = s.get(Booking, choice)
        show_badge(b.status, prefix=f"**Booking #{b.id}** · {b.pet.name} · {b.start_date:%b %d} – {b.end_date:%b %d, %Y} &nbsp; ")
        progress(b.status)
        st.caption(f"{SERVICE_LABELS[b.service_type]} · {b.visits_per_day} visit(s)/day · "
                   f"{b.num_days} day(s) · {b.visit_duration_minutes} min")

        if b.status in PREPARABLE:
            with st.expander("Visit preferences & quote inputs", expanded=b.status == S.DRAFT):
                prepare = _preferences_form(b, actor)
                if prepare:
                    action = ("prepare", prepare)

        _quote_block(b)

        if b.visits:
            st.markdown("**Proposed schedule**")
            table(visit_rows(b.visits), height=min(420, 38 + 35 * len(b.visits)))
            if b.status in (S.PENDING_RESIDENT_APPROVAL, S.NEEDS_RESCHEDULING):
                if st.button("✨ Explain this schedule"):
                    with st.spinner("Summarising..."):
                        text, source = explain_recommendation(recommend_schedule(s, b))
                    st.info(text + ("" if source == "ai" else "\n\n_(template explanation — no AI key configured)_"))

        if b.status == S.NEEDS_RESCHEDULING:
            _alternatives_block(s, b, actor)

        if b.status == S.PENDING_RESIDENT_APPROVAL:
            note("By approving you accept the quoted price and the proposed visit times. A property manager "
                 "then approves the caregiver assignments; only then is the booking confirmed.")
            c1, c2, _ = st.columns([1, 1, 3])
            if c1.button("Approve quote & schedule", type="primary"):
                action = ("approve",)
            if c2.button("Reject"):
                action = ("reject",)
        elif b.status == S.PENDING_ADMIN_APPROVAL:
            st.info("You approved this booking. Waiting for a property manager to approve the caregiver assignments.")
        elif b.status == S.CONFIRMED:
            st.success("Confirmed. Your caregiver(s) are booked for every visit above.")

        if b.status not in TERMINAL:
            with st.expander("Cancel this booking"):
                reason = st.text_input("Reason (optional)", key=f"cancel_reason_{b.id}")
                if st.button("Cancel booking", key=f"cancel_{b.id}"):
                    action = ("cancel", reason)

        with st.expander("Activity"):
            table([{"When": e.created_at, "From": e.from_status or "", "To": e.to_status,
                    "By": e.actor_role, "Note": e.note or ""} for e in b.events], empty="No activity yet.")

    # All writes happen here, after the read session above has closed.
    if action and action[0] == "prepare":
        _run(action[1], "Quote and proposed schedule ready. Review them below.")
    pending = st.session_state.pop("_pending_action", None)
    if pending and pending[0] == "alt":
        _, bid, vid, cid, start = pending
        _run(lambda s: accept_alternative(s, bid, vid, cid, start, actor), "Alternative time accepted.")
    if action == ("approve",):
        _run(lambda s: resident_approve(s, choice, actor), "Approved. A property manager will review the caregiver assignments.")
    elif action == ("reject",):
        _run(lambda s: resident_reject(s, choice, actor), "Booking rejected.")
    elif action and action[0] == "cancel":
        _run(lambda s: cancel_booking(s, choice, actor, action[1] or None), "Booking cancelled.")


# ===================================================================== history
def history_page(resident: Resident) -> None:
    st.subheader(f"Booking history · {resident.full_name}")
    with session_scope() as s:
        rows = list_booking_rows(s, resident.id)
    if not rows:
        st.info("No bookings yet.")
        return
    df = pd.DataFrame(rows)
    m1, m2, m3 = st.columns(3)
    m1.metric("Total bookings", len(df))
    m2.metric("Open", int(df["status"].isin([s.value for s in S if s not in TERMINAL]).sum()))
    m3.metric("Confirmed", int((df["status"] == S.CONFIRMED.value).sum()))
    df["status"] = df["status"].map(lambda v: STATUS_LABELS[S(v)])
    df["photo_updates"] = df["photo_updates"].map({True: "Yes", False: "No"})
    st.dataframe(
        df.rename(columns={
            "id": "ID", "pet": "Pet", "start_date": "Start", "end_date": "End",
            "visits_per_day": "Visits/day", "service_type": "Service", "photo_updates": "Photos",
            "status": "Status", "caregiver": "Caregiver", "special_instructions": "Instructions",
            "created_at": "Created",
        }),
        hide_index=True,
        use_container_width=True,
    )


def render(resident: Resident) -> None:
    show_flash()
    goto = st.session_state.pop("_goto_resident_tab", None)
    if goto:
        st.session_state["resident_tab"] = goto  # set before the widget is created
    unread = resident_care.unread_count(resident)
    bell = f"Notifications ({unread})" if unread else "Notifications"
    tabs = ["New Request", "My Bookings", "Upcoming Visits", "Care Photos", "Daily Reports", bell, "History"]
    if st.session_state.get("resident_tab", "").startswith("Notifications"):
        st.session_state["resident_tab"] = bell  # label changes with the unread count
    tab = st.radio("Resident portal", tabs, horizontal=True, label_visibility="collapsed", key="resident_tab")
    if tab == "New Request":
        new_booking_page(resident)
    elif tab == "My Bookings":
        my_bookings_page(resident)
    elif tab == "Upcoming Visits":
        resident_care.upcoming_visits(resident)
    elif tab == "Care Photos":
        resident_care.care_photos(resident)
    elif tab == "Daily Reports":
        resident_care.daily_reports(resident)
    elif tab.startswith("Notifications"):
        resident_care.notification_center(resident)
    else:
        history_page(resident)
