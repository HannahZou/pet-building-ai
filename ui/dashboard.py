"""Scheduling & Pricing Dashboard (read-only operations view)."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from pawpal.database import session_scope
from pawpal.reports import availability_grid, pricing_rows, status_counts, unresolved_conflicts, visits_in_range

from .common import table


def render(buildings: list[str]) -> None:
    c1, c2, c3 = st.columns([1, 1, 1.2])
    start = c1.date_input("From", value=date.today(), key="db_from")
    end = c2.date_input("To", value=date.today() + timedelta(days=45), key="db_to")
    building = c3.selectbox("Building", ["All buildings"] + buildings, key="db_building")
    bfilter = None if building == "All buildings" else building
    if end < start:
        st.error("The end date is before the start date.")
        return

    with session_scope() as s:
        counts = status_counts(s, bfilter)
        visits = visits_in_range(s, start, end, bfilter)
        grid = availability_grid(s, start, days=min(14, (end - start).days + 1))
        prices = pricing_rows(s, bfilter)
        conflicts = unresolved_conflicts(s, bfilter)

    blocking = [c for c in conflicts if c["Severity"] == "Blocking"]
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Visits in range", len(visits))
    k2.metric("Unassigned visits", sum(v["Caregiver"] == "Unassigned" for v in visits))
    k3.metric("Awaiting approval", counts["Pending Resident Approval"] + counts["Pending Admin Approval"])
    k4.metric("Confirmed bookings", counts["Confirmed"])
    k5.metric("Blocking conflicts", len(blocking))

    tab = st.radio("Dashboard", ["Visit schedule", "Caregiver availability", "Pricing", "Conflicts", "Status"],
                   horizontal=True, label_visibility="collapsed", key="db_tab")
    if tab == "Visit schedule":
        table(visits, empty="No visits in this range.", height=520)
    elif tab == "Caregiver availability":
        st.caption(f"Working hours for the first {len(grid[0]) - 1 if grid else 0} days of the range. OFF = time off, · = not working.")
        table(grid)
    elif tab == "Pricing":
        if prices:
            total = sum(float(p["Total"].strip("$").replace(",", "")) for p in prices if p["Status"] in ("Confirmed", "Completed"))
            st.metric("Confirmed revenue (quoted)", f"${total:,.2f}")
        table(prices, empty="No quotes yet.")
        for p in prices[:30]:
            with st.expander(f"#{p['Booking']} · {p['Pet']} · {p['Total']} · {p['Status']}"):
                table(p["_lines"])
    elif tab == "Conflicts":
        if conflicts:
            table(conflicts)
        else:
            st.success("No unresolved conflicts.")
    else:
        df = pd.DataFrame({"Bookings": counts}).query("Bookings > 0")
        st.bar_chart(df)
        table([{"Status": k, "Bookings": v} for k, v in counts.items()])
