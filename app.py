"""PawPal AI - Streamlit app (Week 1: AI booking foundation).

Run with:  streamlit run app.py
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pawpal.ai_extraction import ExtractionError, extract_booking
from pawpal.booking_service import (
    BookingError,
    create_booking_draft,
    draft_from_extraction,
    list_booking_rows,
    list_pets_for_resident,
    list_residents,
    review_extraction,
    validate_draft,
)
from pawpal.database import init_db, session_scope
from pawpal.models import ServiceType
from pawpal.schemas import SERVICE_LABELS, BookingDraft
from pawpal.seed_data import seed_database

EXAMPLE = (
    "I'm traveling from November 6 to November 9, 2026. My cat Luna needs feeding "
    "twice a day and I'd like a photo after every visit."
)
PHOTO_OPTIONS = ["Not specified", "Yes", "No"]

st.set_page_config(page_title="PawPal AI", page_icon="🐾", layout="wide")
st.markdown(
    """
<style>
  .block-container {padding-top: 2rem; max-width: 1100px;}
  .pp-hero {background: linear-gradient(135deg,#1f2a44 0%,#3b4a73 100%);
            color:#fff; padding:1.6rem 2rem; border-radius:18px; margin-bottom:1.5rem;}
  .pp-hero h1 {margin:0; font-size:2rem; letter-spacing:.5px; color:#fff;}
  .pp-hero p {margin:.3rem 0 0; color:#d8dcec;}
  .pp-note {border-left:4px solid #c9a24b; background:#faf6ea; color:#4a3f1f;
            padding:.7rem 1rem; border-radius:8px; margin:.5rem 0 1rem;}
</style>
<div class="pp-hero"><h1>🐾 PawPal AI</h1>
<p>Concierge pet care, in the comfort of home.</p></div>
""",
    unsafe_allow_html=True,
)

init_db()


# ------------------------------------------------------------------ helpers
def _reset_draft() -> None:
    for key in ("draft", "ai_questions", "pet_mismatch"):
        st.session_state.pop(key, None)


def _new_draft(draft: BookingDraft, questions=None, mismatch=None) -> None:
    st.session_state["draft"] = draft
    st.session_state["draft_id"] = st.session_state.get("draft_id", 0) + 1  # fresh widget keys
    st.session_state["ai_questions"] = questions or []
    st.session_state["pet_mismatch"] = mismatch


# --------------------------------------------------------------------- data
with session_scope() as s:
    residents = list_residents(s)

if not residents:
    st.info("No demo data yet. Load the synthetic residents, pets and booking history to begin.")
    if st.button("Load demo data", type="primary"):
        seed_database()
        st.rerun()
    st.stop()

with st.sidebar:
    st.markdown("### 🐾 PawPal AI")
    page = st.radio("Navigate", ["New Booking", "Booking History"], label_visibility="collapsed")
    st.divider()
    st.caption("Demo mode - choose a resident")
    _by_id = {r.id: r for r in residents}
    resident = _by_id[
        st.selectbox(
            "Resident",
            list(_by_id),
            format_func=lambda rid: (
                f"{_by_id[rid].full_name} · {_by_id[rid].building_name} {_by_id[rid].apartment_number}"
            ),
        )
    ]

with session_scope() as s:
    pets = list_pets_for_resident(s, resident.id)


# -------------------------------------------------------------- booking page
def booking_page() -> None:
    flash = st.session_state.pop("flash", None)
    if flash:
        st.success(flash)

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

    request_text = st.text_area(
        "Tell us about your trip",
        height=130,
        placeholder=EXAMPLE,
        key="request_text",
    )
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
            _new_draft(draft_from_extraction(extracted), extracted.clarification_questions, mismatch)
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
    notes = st.text_area(
        "Special instructions", value=draft.special_instructions or "", key=f"notes_{n}"
    )

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

    st.markdown(
        '<div class="pp-note">Saving creates a <b>pending</b> draft only. No caregiver is assigned '
        "and the booking is <b>not confirmed</b>.</div>",
        unsafe_allow_html=True,
    )
    if st.button("Confirm Booking Draft", type="primary", disabled=bool(errors)):
        try:
            with session_scope() as s:
                booking = create_booking_draft(s, resident.id, pet.id, current)
                booking_id = booking.id
        except BookingError as exc:
            st.error(str(exc))
        else:
            _reset_draft()
            st.session_state["flash"] = (
                f"Booking draft #{booking_id} saved with status PENDING. "
                "No caregiver has been assigned and it is not yet confirmed."
            )
            st.rerun()


# ------------------------------------------------------------ history page
def history_page() -> None:
    st.subheader(f"Booking history · {resident.full_name}")
    with session_scope() as s:
        rows = list_booking_rows(s, resident.id)
    if not rows:
        st.info("No bookings yet.")
        return
    df = pd.DataFrame(rows)
    m1, m2 = st.columns(2)
    m1.metric("Total bookings", len(df))
    m2.metric("Pending", int((df["status"] == "pending").sum()))
    df["status"] = df["status"].str.upper()
    df["photo_updates"] = df["photo_updates"].map({True: "Yes", False: "No"})
    st.dataframe(
        df.rename(
            columns={
                "id": "ID", "pet": "Pet", "start_date": "Start", "end_date": "End",
                "visits_per_day": "Visits/day", "service_type": "Service",
                "photo_updates": "Photos", "status": "Status", "caregiver": "Caregiver",
                "special_instructions": "Instructions", "created_at": "Created",
            }
        ),
        hide_index=True,
        use_container_width=True,
    )


booking_page() if page == "New Booking" else history_page()
