"""Analytics portal (Week 4): executive dashboard, care operations, AI evaluation, insights.

Every number comes from ``pawpal.analytics`` under the viewer's access scope;
the backend raises AuthorizationError for anything outside it, so hiding a widget
is never the only protection.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import select

from pawpal import config
from pawpal.access import all_buildings, analytics_scope
from pawpal.actors import Actor, AuthorizationError
from pawpal.analytics.data import AnalyticsFilter
from pawpal.analytics.insights import generate_insights
from pawpal.analytics.kpis import KPI_DEFINITIONS, compute_snapshot
from pawpal.analytics.monitoring import AlertThresholds, operational_alerts
from pawpal.analytics.monthly_report import build_monthly_report, export, month_filter, pdf_available
from pawpal.database import session_scope
from pawpal.models import BookingStatus, EvaluationRun, PlatformAdmin, PropertyManager, ServiceType
from pawpal.schemas import SERVICE_LABELS
from pawpal.timeutil import default_timezone, local_today
from pawpal.workflow import STATUS_LABELS

from .common import GOLD, NAVY, done, note, show_flash, table

PALETTE = [NAVY, GOLD, "#3b4a73", "#8a9bc4", "#b98b2e", "#5d6b8f", "#d8c08a"]
SECTIONS = ["Executive Dashboard", "Care Operations", "AI Agent Evaluation", "Business Insights"]
SEVERITY = {"critical": ("🔴", "error"), "warning": ("🟠", "warning"), "info": ("🔵", "info")}


def _layout(fig: go.Figure, height: int = 320) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=40, b=10), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(size=12), legend=dict(orientation="h", y=-0.2),
                      colorway=PALETTE)
    return fig


def _cards(snapshot, keys: list[str], per_row: int = 4) -> None:
    for i in range(0, len(keys), per_row):
        cols = st.columns(per_row)
        for col, key in zip(cols, keys[i:i + per_row]):
            m = snapshot.metrics[key]
            d = KPI_DEFINITIONS[key]
            basis = f"{m.numerator:g} / {m.denominator:g}" if m.numerator is not None and m.denominator else ""
            col.metric(m.label, m.display(), help=f"{d.formula}. Window: {d.window}. {m.note}".strip())
            if basis:
                col.caption(basis)
            elif m.status != "ok":
                col.caption(m.note[:80])


def _viewer() -> tuple[Actor, str] | None:
    with session_scope() as s:
        managers = [(m.id, f"{m.full_name} · {m.building_name or 'All buildings'}") for m in
                    s.scalars(select(PropertyManager).order_by(PropertyManager.full_name))]
        admins = [(a.id, f"{a.full_name} · admin ({', '.join(a.permissions) or 'no permissions'})") for a in
                  s.scalars(select(PlatformAdmin).order_by(PlatformAdmin.full_name))]
    options = [("property_manager", i, label) for i, label in managers] + [("platform_admin", i, label) for i, label in admins]
    if not options:
        st.info("No property managers or administrators yet. Load the demo data first.")
        return None
    with st.sidebar:
        idx = st.selectbox("View analytics as", range(len(options)), format_func=lambda i: options[i][2], key="an_viewer")
    role, vid, label = options[idx]
    return Actor(role, vid), label


def _filters(actor: Actor):
    with session_scope() as s:
        scope = analytics_scope(s, actor)
        buildings = [b for b in all_buildings(s) if scope.allows(b)]
    today = local_today(ZoneInfo(default_timezone()))
    with st.expander("Filters", expanded=True):
        c1, c2, c3 = st.columns([1, 1, 1.2])
        start = c1.date_input("From", value=date(2026, 7, 1) if today > date(2026, 7, 1) else today - timedelta(days=90), key="an_from")
        end = c2.date_input("To", value=today, key="an_to")
        source = c3.radio("Data source", ["Synthetic (demo)", "Real operations"], horizontal=True, key="an_source",
                          help="Synthetic demo records and real records are never combined.")
        c4, c5, c6 = st.columns(3)
        picked = c4.multiselect("Buildings", buildings, default=[], placeholder="All in your scope", key="an_buildings")
        services = c5.multiselect("Service type", list(ServiceType), format_func=lambda x: SERVICE_LABELS[x].split(" (")[0],
                                  placeholder="All services", key="an_services")
        statuses = c6.multiselect("Booking status", list(BookingStatus), format_func=lambda x: STATUS_LABELS[x],
                                  placeholder="All statuses", key="an_statuses")
    if end < start:
        st.error("The end date is before the start date.")
        return None, scope
    f = AnalyticsFilter(start, end, tuple(picked) or None, tuple(services) or None, tuple(statuses) or None,
                        "synthetic" if source.startswith("Synthetic") else "real")
    if f.data_source == "synthetic":
        note("<b>Synthetic demo data.</b> These figures come from generated, fictional records "
             "(residents flagged <code>is_synthetic</code>) and do not describe real operations.")
    if statuses:
        st.caption("A booking-status filter applies to every metric, so rates such as conversion are conditional on it.")
    return f, scope


# ------------------------------------------------------------------ sections
def executive(actor: Actor, f: AnalyticsFilter) -> None:
    with session_scope() as s:
        snap = compute_snapshot(s, actor, f)
    cov = snap.coverage
    st.caption(f"Period {cov['period']} · {cov['bookings']} bookings · {cov['visits']} visits · "
               f"buildings: {', '.join(cov['buildings']) or 'none'}")
    if not cov["bookings"] and not cov["visits"]:
        st.info("No records match these filters.")
    st.markdown("##### Business performance")
    _cards(snap, ["total_bookings", "confirmed_bookings", "completed_bookings", "booking_conversion_rate",
                  "gross_booking_value", "average_booking_value"], per_row=3)
    st.markdown("##### Customer experience")
    _cards(snap, ["repeat_booking_rate", "resident_retention_rate", "avg_report_delivery_hours",
                  "notification_success_rate", "customer_satisfaction"], per_row=5)

    weekly = pd.DataFrame(snap.weekly)
    c1, c2 = st.columns(2)
    if not weekly.empty:
        fig = go.Figure()
        fig.add_bar(x=weekly["week"], y=weekly["bookings"], name="Bookings", marker_color="#8a9bc4")
        fig.add_scatter(x=weekly["week"], y=weekly["confirmed"], name="Confirmed", mode="lines+markers", line_color=NAVY)
        c1.plotly_chart(_layout(fig.update_layout(title="Bookings per week (by start date)")), use_container_width=True)
        fig = px.area(weekly, x="week", y="gbv", title="Gross booking value per week ($)", color_discrete_sequence=[GOLD])
        c2.plotly_chart(_layout(fig), use_container_width=True)
    if snap.revenue_by_building:
        rb = pd.DataFrame({"Building": list(snap.revenue_by_building), "GBV": list(snap.revenue_by_building.values())})
        fig = px.bar(rb.sort_values("GBV"), x="GBV", y="Building", orientation="h", title="Revenue by building ($)",
                     color_discrete_sequence=[NAVY])
        c1.plotly_chart(_layout(fig), use_container_width=True)
        rs = pd.DataFrame({"Service": [k.replace("_", " ") for k in snap.revenue_by_service],
                           "GBV": list(snap.revenue_by_service.values())})
        fig = px.pie(rs, names="Service", values="GBV", title="Revenue by service type", hole=0.55,
                     color_discrete_sequence=PALETTE)
        c2.plotly_chart(_layout(fig), use_container_width=True)
    st.markdown("##### Building performance")
    table(snap.building_table, empty="No building data for these filters.")
    with st.expander("KPI definitions"):
        table([{"KPI": d.label, "Formula": d.formula, "Window": d.window, "Excludes": d.exclusions, "Missing data": d.missing}
               for d in KPI_DEFINITIONS.values()])


def operations(actor: Actor, f: AnalyticsFilter) -> None:
    with session_scope() as s:
        snap = compute_snapshot(s, actor, f)
        alerts = operational_alerts(s, actor, f)
    st.markdown("##### Operational performance")
    _cards(snap, ["total_scheduled_visits", "completed_visits", "missed_visits", "visit_completion_rate",
                  "caregiver_utilization_rate", "scheduling_conflict_rate", "avg_booking_processing_hours",
                  "avg_assignment_hours"])
    st.markdown("##### Alerts")
    st.caption("Alerts never change bookings or assignments; each suggests an action for an authorised person.")
    if not alerts:
        st.success("No alerts for these filters.")
    for a in alerts:
        icon, kind = SEVERITY[a.severity]
        getattr(st, kind)(f"{icon} **{a.title}** — {a.detail}  \n_Suggested:_ {a.action}")
    with st.expander("Alert thresholds"):
        t = AlertThresholds.load()
        table([{"Threshold": k.replace("_", " "), "Value": str(v)} for k, v in t.__dict__.items()])
        st.caption("Override with a JSON file referenced by PAWPAL_ALERT_THRESHOLDS.")
    c1, c2 = st.columns(2)
    wd = pd.DataFrame({"Day": list(snap.weekday_visits), "Visits": list(snap.weekday_visits.values())})
    if wd["Visits"].sum():
        fig = px.bar(wd, x="Day", y="Visits", title="Visits by weekday", color_discrete_sequence=[NAVY])
        c1.plotly_chart(_layout(fig), use_container_width=True)
    cg = pd.DataFrame(snap.caregiver_table)
    if not cg.empty:
        cg["Utilization %"] = (cg["Assigned h"] / cg["Available h"].where(cg["Available h"] > 0) * 100).round(1)
        fig = px.bar(cg.sort_values("Utilization %"), x="Utilization %", y="Caregiver", orientation="h",
                     title="Caregiver utilization (assigned ÷ available hours)", color_discrete_sequence=[GOLD])
        c2.plotly_chart(_layout(fig), use_container_width=True)
    st.markdown("##### Caregiver workload")
    table(snap.caregiver_table, empty="No caregivers in scope.")


def evaluation(actor: Actor, f: AnalyticsFilter, scope) -> None:
    if not scope.can_read_evaluation:
        st.warning("Your account does not have the evaluation:read permission.")
        return
    with session_scope() as s:
        runs = s.scalars(select(EvaluationRun).order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc())).all()
        runs = [(r.id, r.kind, r.system, r.dataset_version, r.dataset_size, r.metrics, r.failures, r.created_at) for r in runs]
        snap = compute_snapshot(s, actor, f)
    booking = next((r for r in runs if r[1] == "booking_extraction"), None)
    st.markdown("##### Booking extraction agent (Week 1)")
    if booking:
        _, _, system, version, size, m, failures, created = booking
        st.caption(f"Latest run: **{system}** on {size} hand-labelled synthetic requests ({version}) · {created:%Y-%m-%d %H:%M} UTC")
        if system.startswith("rules"):
            st.info("This is the rule-based **baseline**, not the LLM. Run the OpenAI benchmark to measure the agent itself.")
        cols = st.columns(4)
        for col, (label, key) in zip(cols * 3, [("Field accuracy", "field_accuracy"), ("Exact match", "exact_match_accuracy"),
                                                 ("Pet name", "pet_name_accuracy"), ("Dates", "date_accuracy"),
                                                 ("Visits/day", "visits_per_day_accuracy"), ("Service type", "service_type_accuracy"),
                                                 ("Missing-info F1", "missing_info_f1"), ("Invalid range caught", "invalid_range_detection")]):
            col.metric(label, "—" if m.get(key) is None else f"{m[key]:.1%}")
        pc = pd.DataFrame([{"Category": c.replace("_", " "), "Cases": v["cases"], "Exact match": v["exact_match"],
                            "Field accuracy": v["field_accuracy"]} for c, v in m["per_category"].items()])
        fig = px.bar(pc, x="Category", y="Field accuracy", title="Field accuracy by case category",
                     color_discrete_sequence=[NAVY], range_y=[0, 1])
        st.plotly_chart(_layout(fig), use_container_width=True)
        with st.expander(f"Failure cases ({len(failures)})"):
            table([{"Case": x["id"], "Category": x["category"], "Wrong fields": ", ".join(x["diffs"]) or "missing-info set"}
                   for x in failures])
    else:
        st.info("No booking-extraction run stored yet.")

    st.markdown("##### Care report agent (Week 3)")
    _cards(snap, ["report_unsupported_claim_rate", "ai_draft_unsupported_claim_rate", "missing_activity_detection",
                  "human_correction_rate"])
    care = [r for r in runs if r[1] == "care_reports"]
    latest = {}
    for r in care:
        latest.setdefault(r[2], r)
    table([{"System": sysname, "Cases": r[4], "Factual consistency": r[5].get("factual_consistency"),
            "Unsupported claim rate": r[5].get("unsupported_claim_rate"),
            "Missing-activity detection": r[5].get("missing_activity_detection"),
            "Completeness": r[5].get("report_completeness"), "AI text rejected": r[5].get("ai_text_rejected_by_grounding_check"),
            "Run": f"{r[7]:%Y-%m-%d}"} for sysname, r in latest.items()],
          empty="No care-report evaluation stored yet.")
    st.caption("“adversarial” is a scripted stub that invents facts on purpose; it measures the grounding check, not report quality.")

    if scope.can_run_evaluation:
        st.markdown("##### Run evaluations")
        c1, c2, c3 = st.columns(3)
        action = None
        if c1.button("Booking benchmark (baseline)"):
            action = "booking_baseline"
        if c2.button("Care reports (template + adversarial)"):
            action = "care"
        if c3.button("Booking benchmark (OpenAI)", disabled=not config.openai_api_key(),
                     help=None if config.openai_api_key() else "Add OPENAI_API_KEY to .env to evaluate the LLM."):
            action = "booking_openai"
        if action:
            from pawpal.agent_eval import booking as bench, care_reports as care_eval

            with st.spinner("Running evaluation..."):
                if action == "booking_baseline":
                    bench.run(*bench.baseline_predictor())
                elif action == "booking_openai":
                    bench.run(*bench.openai_predictor())
                else:
                    care_eval.run("template")
                    care_eval.run("adversarial")
            done("Evaluation finished and stored.")
    st.markdown("##### Evaluation history")
    table([{"Run": r[0], "When": f"{r[7]:%Y-%m-%d %H:%M}", "Kind": r[1].replace("_", " "), "System": r[2],
            "Dataset": f"{r[3]} ({r[4]})",
            "Headline": (f"field acc {r[5].get('field_accuracy')}" if r[1] == "booking_extraction"
                         else f"consistency {r[5].get('factual_consistency')}")} for r in runs],
          empty="No evaluation runs yet.")


def insights(actor: Actor, f: AnalyticsFilter, scope) -> None:
    st.markdown("##### AI management summary")
    st.caption("The model only sees verified KPI results (no database access) and its text is checked: every number "
               "must come from the data, no causes are claimed, and trends need enough data.")
    if st.button("Generate summary", type="primary"):
        with session_scope() as s:
            st.session_state["an_insights"] = generate_insights(s, actor, f)
    res = st.session_state.get("an_insights")
    if res is not None:
        src = {"ai": f"AI ({res.model})", "template": "template (no API key)",
               "template_fallback": "template - the AI draft failed the grounding check"}[res.source]
        st.markdown(f"**{res.summary.headline}**  \n_Source: {src} · period {res.facts['period']} · "
                    f"{res.facts['coverage']['bookings']} bookings · {res.facts['data_source']} data_")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Observations** (what the data shows)")
            for o in res.summary.observations:
                st.write(f"- {o.text}")
        with c2:
            st.markdown("**Recommendations** (for your decision)")
            for r in res.summary.recommendations or []:
                st.write(f"- {r.text}")
            if not res.summary.recommendations:
                st.write("- None suggested.")
        if res.summary.data_gaps:
            with st.expander(f"Data gaps ({len(res.summary.data_gaps)})"):
                for g in res.summary.data_gaps:
                    st.write(f"- {g}")
        if res.unsupported:
            with st.expander("Why the AI draft was rejected"):
                for u in res.unsupported:
                    st.write(f"- {u}")

    st.markdown("##### Monthly operations report")
    today = local_today(ZoneInfo(default_timezone()))
    c1, c2 = st.columns(2)
    year = c1.selectbox("Year", [2026, 2027, 2025], index=0, key="an_rep_year")
    month = c2.selectbox("Month", list(range(1, 13)), index=max(0, min(11, today.month - 2)) if year == today.year else 8,
                         format_func=lambda m: date(2026, m, 1).strftime("%B"), key="an_rep_month")
    if st.button("Preview report"):
        rf = month_filter(year, month, buildings=f.buildings, service_types=f.service_types, statuses=f.statuses,
                          data_source=f.data_source)
        with session_scope() as s:
            st.session_state["an_report"] = build_monthly_report(s, actor, rf)
    rep = st.session_state.get("an_report")
    if rep is None:
        return
    st.caption("Reports are never e-mailed automatically. Exports contain aggregates only (no resident details).")
    if scope.can_export:
        stem = f"pawpal-operations-{rep.period.split(' ')[0][:7]}"
        c1, c2, c3 = st.columns(3)
        with session_scope() as s:
            c1.download_button("Download Markdown", export(s, actor, rep, "md"), f"{stem}.md", "text/markdown")
            c2.download_button("Download CSV (KPIs)", export(s, actor, rep, "csv"), f"{stem}.csv", "text/csv")
            if pdf_available():
                c3.download_button("Download PDF", export(s, actor, rep, "pdf"), f"{stem}.pdf", "application/pdf")
            else:
                c3.caption("PDF export: `pip install reportlab`")
    else:
        st.info("Your account does not have the reports:export permission.")
    with st.container(border=True):
        st.markdown(rep.markdown)


def render() -> None:
    show_flash()
    viewer = _viewer()
    if viewer is None:
        return
    actor, label = viewer
    try:
        f, scope = _filters(actor)
    except AuthorizationError as exc:
        st.error(str(exc))
        return
    if f is None:
        return
    section = st.radio("Analytics", SECTIONS, horizontal=True, label_visibility="collapsed", key="an_section")
    try:
        if section == "Executive Dashboard":
            executive(actor, f)
        elif section == "Care Operations":
            operations(actor, f)
        elif section == "AI Agent Evaluation":
            evaluation(actor, f, scope)
        else:
            insights(actor, f, scope)
    except AuthorizationError as exc:
        st.error(str(exc))
