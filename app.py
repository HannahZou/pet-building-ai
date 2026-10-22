"""PawPal AI - Streamlit app.

Week 1: AI booking requests. Week 2: scheduling, pricing and approvals.
Week 3: care reporting. Week 4: analytics, AI evaluation and insights.
Run with:  streamlit run app.py

UI code lives in ``ui/``; business logic in ``pawpal/`` (no Streamlit imports there).
"""
from __future__ import annotations

import streamlit as st
from sqlalchemy import select

from pawpal.availability import list_caregivers
from pawpal.booking_service import list_residents
from pawpal.database import init_db, session_scope
from pawpal.models import PropertyManager, Resident
from pawpal.seed_data import seed_all
from pawpal.workflow import list_managers
from ui import analytics, caregiver, dashboard, manager, resident
from ui.common import hero

st.set_page_config(page_title="PawPal AI", page_icon="🐾", layout="wide")

PORTALS = {
    "Resident Portal": "Concierge pet care, in the comfort of home.",
    "Caregiver Portal": "Your visits, hours and time off.",
    "Property Manager Portal": "Review assignments, resolve conflicts, approve bookings.",
    "Scheduling & Pricing": "Operations view across buildings.",
    "Analytics": "Property analytics, AI evaluation and operational intelligence.",
}

init_db()  # creates the schema or upgrades a Week 1 database in place

with session_scope() as s:
    residents = list_residents(s)
    has_week2 = s.scalar(select(PropertyManager.id).limit(1)) is not None

if not residents or not has_week2:
    hero("PawPal AI", "Concierge pet care, in the comfort of home.")
    st.info(
        "Load the synthetic demo data: residents, pets, caregivers, booking history, caregiver "
        "availability, property managers and bookings at every approval stage. Safe to run more than once."
    )
    if st.button("Load demo data", type="primary"):
        with st.spinner("Creating demo data..."):
            seed_all()
        st.rerun()
    st.stop()

with st.sidebar:
    st.markdown("### 🐾 PawPal AI")
    portal = st.radio("Portal", list(PORTALS), label_visibility="collapsed", key="portal")
    st.divider()
    st.caption("Demo mode — choose who you are")

hero(portal.replace(" Portal", ""), PORTALS[portal])

with session_scope() as s:
    buildings = sorted(set(s.scalars(select(Resident.building_name))))

if portal == "Resident Portal":
    _by_id = {r.id: r for r in residents}
    with st.sidebar:
        rid = st.selectbox(
            "Resident",
            list(_by_id),
            format_func=lambda i: f"{_by_id[i].full_name} · {_by_id[i].building_name} {_by_id[i].apartment_number}",
            key="resident_pick",
        )
    resident.render(_by_id[rid])
elif portal == "Caregiver Portal":
    with session_scope() as s:
        cgs = list_caregivers(s)
    caregiver.render(cgs, buildings)
elif portal == "Property Manager Portal":
    with session_scope() as s:
        managers = list_managers(s)
    manager.render(managers)
elif portal == "Analytics":
    analytics.render()
else:
    dashboard.render(buildings)
