"""Monthly property operations report (preview + Markdown / CSV / optional PDF export).

Aggregates only: no resident names, apartments, contact details, notes or
medical information appear in the report or its exports. Caregiver names (staff)
appear in the utilization table. Reports are never e-mailed automatically.
"""
from __future__ import annotations

import calendar
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access import analytics_scope, require
from ..actors import Actor
from ..models import EvaluationRun
from .data import AnalyticsFilter
from .insights import InsightResult, generate_insights
from .kpis import KPI_DEFINITIONS, KPISnapshot, compute_snapshot
from .monitoring import operational_alerts


def month_filter(year: int, month: int, **kwargs) -> AnalyticsFilter:
    return AnalyticsFilter(date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]), **kwargs)


@dataclass
class MonthlyReport:
    title: str
    period: str
    data_source: str
    generated_at: datetime
    markdown: str
    kpi_rows: list[dict[str, Any]]
    insights: InsightResult
    snapshot: KPISnapshot

    def csv(self) -> str:
        buf = io.StringIO()
        cols = ["metric", "value", "raw_value", "numerator", "denominator", "status", "formula", "window", "note"]
        writer = csv.DictWriter(buf, fieldnames=["period", "data_source"] + cols)
        writer.writeheader()
        for row in self.kpi_rows:
            writer.writerow({"period": self.period, "data_source": self.data_source, **{c: row.get(c) for c in cols}})
        return buf.getvalue()


def _table(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["_No data._"]
    cols = list(rows[0])
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    return out


def _kpi_lines(snap: KPISnapshot, keys: list[str]) -> list[str]:
    rows = []
    for k in keys:
        m = snap.metrics[k]
        basis = f"{m.numerator:g} / {m.denominator:g}" if m.numerator is not None and m.denominator else ""
        rows.append({"KPI": m.label, "Value": m.display(), "Basis": basis, "Note": m.note or ""})
    return _table(rows)


def build_monthly_report(session: Session, actor: Actor, f: AnalyticsFilter, client: Any | None = None,
                         use_ai: bool = True, as_of: datetime | None = None) -> MonthlyReport:
    scope = analytics_scope(session, actor, f.buildings)
    snap = compute_snapshot(session, actor, f, as_of)
    alerts = operational_alerts(session, actor, f, now=as_of)
    insights = generate_insights(session, actor, f, client=client, use_ai=use_ai, as_of=as_of)
    s = insights.summary
    runs = session.scalars(select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(6)).all() \
        if scope.can_read_evaluation else []
    scope_txt = "all buildings" if snap.scope_buildings is None else ", ".join(snap.scope_buildings)
    cov = snap.coverage
    md = [
        f"# PawPal AI - Monthly Property Operations Report",
        f"**Period:** {cov['period']}  ·  **Buildings:** {scope_txt}  ·  **Data:** {cov['data_source']}"
        + ("  ·  ⚠️ synthetic demo data, not real operations" if cov["data_source"] == "synthetic" else ""),
        f"**Coverage:** {cov['bookings']} bookings, {cov['visits']} visits, {cov['reports']} care reports, "
        f"{cov['caregivers']} caregivers  ·  generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        "", "## 1. Executive summary", f"_{s.headline}_ (summary source: {insights.source})", "",
        "**Observations**", *[f"- {o.text}" for o in s.observations], "",
        "## 2. Booking performance",
        *_kpi_lines(snap, ["total_bookings", "confirmed_bookings", "completed_bookings", "booking_conversion_rate",
                           "avg_booking_processing_hours", "avg_assignment_hours", "scheduling_conflict_rate"]),
        "", "## 3. Revenue overview",
        *_kpi_lines(snap, ["gross_booking_value", "average_booking_value"]), "",
        "**By building**", *_table([{"Building": k, "GBV": f"${v:,.2f}"} for k, v in sorted(snap.revenue_by_building.items())]), "",
        "**By service**", *_table([{"Service": k.replace("_", " "), "GBV": f"${v:,.2f}"} for k, v in sorted(snap.revenue_by_service.items())]),
        "", "## 4. Caregiver utilization",
        *_kpi_lines(snap, ["caregiver_utilization_rate"]), "", *_table(snap.caregiver_table),
        "", "## 5. Service completion",
        *_kpi_lines(snap, ["total_scheduled_visits", "completed_visits", "missed_visits", "visit_completion_rate"]),
        "", *_table(snap.building_table),
        "", "## 6. Customer experience",
        *_kpi_lines(snap, ["repeat_booking_rate", "resident_retention_rate", "avg_report_delivery_hours",
                           "notification_success_rate", "customer_satisfaction"]),
        "", "## 7. AI agent evaluation",
        *_kpi_lines(snap, ["booking_extraction_accuracy", "report_unsupported_claim_rate", "ai_draft_unsupported_claim_rate",
                           "missing_activity_detection", "human_correction_rate"]),
        "", "**Latest evaluation runs**",
        *_table([{"Kind": r.kind, "System": r.system, "Dataset": f"{r.dataset_version} ({r.dataset_size})",
                  "Key result": _key_result(r), "Run": f"{r.created_at:%Y-%m-%d}"} for r in runs]),
        "", "## 8. Operational risks",
        *([f"- **{a.severity.upper()}** {a.title} - {a.detail} _Suggested: {a.action}_" for a in alerts] or ["- No alerts."]),
        "", "## 9. Recommended actions",
        "_Suggestions for human review; nothing is changed automatically._",
        *([f"- {r.text}" for r in s.recommendations] or ["- No specific actions suggested."]),
        "", "## Data gaps", *([f"- {g}" for g in s.data_gaps] or ["- None."]),
        "", "---", "_All figures are computed from PawPal records; definitions in docs/KPI_DEFINITIONS.md._",
    ]
    return MonthlyReport(
        title="Monthly Property Operations Report", period=cov["period"], data_source=cov["data_source"],
        generated_at=datetime.now(timezone.utc), markdown="\n".join(md), kpi_rows=snap.metric_rows(),
        insights=insights, snapshot=snap,
    )


def _key_result(run: EvaluationRun) -> str:
    m = run.metrics
    if run.kind == "booking_extraction":
        return f"field accuracy {m.get('field_accuracy')}, exact match {m.get('exact_match_accuracy')}"
    return f"consistency {m.get('factual_consistency')}, completeness {m.get('report_completeness')}"


def export(session: Session, actor: Actor, report: MonthlyReport, fmt: str) -> bytes:
    """Markdown / CSV / PDF bytes. Requires export permission (managers: own buildings)."""
    scope = analytics_scope(session, actor, report.snapshot.filter.buildings)
    require(scope, "can_export")
    if fmt == "md":
        return report.markdown.encode("utf-8")
    if fmt == "csv":
        return report.csv().encode("utf-8")
    if fmt == "pdf":
        return _pdf(report)
    raise ValueError("Unknown export format.")


def pdf_available() -> bool:
    try:
        import reportlab  # noqa: F401
    except ImportError:
        return False
    return True


def _pdf(report: MonthlyReport) -> bytes:
    """Simple text PDF of the Markdown report (needs the optional ``reportlab`` package)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    y = height - 54
    for raw in report.markdown.splitlines():
        line = raw.replace("**", "").replace("_", "").replace("⚠️", "!")
        size, font = (14, "Helvetica-Bold") if line.startswith("#") else (8.5, "Helvetica")
        line = line.lstrip("# ")
        for chunk in [line[i:i + 120] for i in range(0, max(len(line), 1), 120)]:
            if y < 54:
                c.showPage()
                y = height - 54
            c.setFont(font, size)
            c.drawString(40, y, chunk)
            y -= size + 4
    c.save()
    return buf.getvalue()
