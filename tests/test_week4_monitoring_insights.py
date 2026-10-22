"""Alerts, business insights (grounding) and the monthly report."""
import csv
import io
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from pawpal.actors import Actor, AuthorizationError
from pawpal.analytics.data import AnalyticsFilter
from pawpal.analytics.insights import InsightItem, InsightSummary, build_facts, check_summary, generate_insights, template_summary
from pawpal.analytics.kpis import KPI_DEFINITIONS, compute_snapshot
from pawpal.analytics.monitoring import AlertThresholds, operational_alerts
from pawpal.analytics.monthly_report import build_monthly_report, export, month_filter, pdf_available
from pawpal.care_logs import log_activity
from pawpal.care_reports import generate_daily_report
from pawpal.database import session_scope
from pawpal.models import ActivityType as A, CompletionStatus as C, DailyCareReport, PlatformAdmin, Resident
from pawpal.notifications import MockEmailProvider, deliver_pending
from pawpal.visit_execution import check_in, check_out, run_daily_checks
from tests import factories as f
from tests.conftest import FakeOpenAI
from tests.week3_world import DAY1, DAY2, at, confirmed_world

NOV = AnalyticsFilter(date(2026, 11, 1), date(2026, 11, 30), data_source="real")
NOW = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)


def _operations(engine, w):
    """Nov 6: morning checked in 45 min late; evening missed. One email failed."""
    m1, _ = w.visits[DAY1]
    with session_scope(engine) as s:
        check_in(s, m1, w.caregiver, now=at(DAY1, 7, 45))   # scheduled 07:00
        log_activity(s, m1, w.caregiver, A.FEEDING, C.COMPLETED)
        log_activity(s, m1, w.caregiver, A.FRESH_WATER, C.COMPLETED)
        check_out(s, m1, w.caregiver, now=at(DAY1, 8, 10))
    with session_scope(engine) as s:
        run_daily_checks(s, now=at(DAY2, 8))
    with session_scope(engine) as s:
        deliver_pending(s, MockEmailProvider(fail_times=1))
        from pawpal.models import Notification
        for n in s.query(Notification):  # stamp on the visit day so tests never depend on today's date
            n.created_at = at(DAY1, 9)


def test_alert_thresholds(file_engine):
    w = confirmed_world(file_engine)
    _operations(file_engine, w)
    with session_scope(file_engine) as s:
        alerts = operational_alerts(s, w.admin_actor, NOV, AlertThresholds(late_checkins_warning=1), now=NOW)
        cats = {a.category: a for a in alerts}
        assert cats["Missed visits"].count == 1 and cats["Missed visits"].severity == "critical"
        assert cats["Late check-ins"].count == 1
        assert cats["Failed notifications"].count == 1
        quiet = operational_alerts(s, w.admin_actor, NOV, AlertThresholds(
            missed_visits_warning=5, late_checkin_minutes=60, failed_notifications_warning=10), now=NOW)
        assert not {"Missed visits", "Late check-ins", "Failed notifications"} & {a.category for a in quiet}


def test_alert_thresholds_from_json(tmp_path, monkeypatch):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"late_checkin_minutes": 15, "unknown_key": 1}))
    monkeypatch.setenv("PAWPAL_ALERT_THRESHOLDS", str(path))
    t = AlertThresholds.load()
    assert t.late_checkin_minutes == 15 and t.missed_visits_warning == 1


def test_report_backlog_and_confirmation_delay_alerts(file_engine):
    w = confirmed_world(file_engine, confirm=False)  # waiting for admin approval
    with session_scope(file_engine) as s:
        alerts = operational_alerts(s, w.admin_actor, NOV, now=datetime.now(timezone.utc) + timedelta(hours=72))
    assert "Confirmation delays" in {a.category for a in alerts}
    w2 = confirmed_world(file_engine, building=f.B2)
    with session_scope(file_engine) as s:
        v = w2.visits[DAY1][0]
        check_in(s, v, w2.caregiver, now=at(DAY1, 7))
        generate_daily_report(s, w2.booking, DAY1)
    with session_scope(file_engine) as s:
        later = operational_alerts(s, w2.admin_actor, NOV, now=datetime.now(timezone.utc) + timedelta(hours=30))
        assert "Reports pending approval" in {a.category for a in later}


def test_alerts_respect_building_scope(file_engine):
    w = confirmed_world(file_engine)
    _operations(file_engine, w)
    with session_scope(file_engine) as s:
        b2 = f.manager(s, building=f.B2, name="B2 Manager").id
        assert operational_alerts(s, Actor.manager(b2), NOV, now=NOW) == []
        with pytest.raises(AuthorizationError):
            operational_alerts(s, w.res_actor, NOV, now=NOW)


# ------------------------------------------------------------------ insights
def _facts(engine, w):
    with session_scope(engine) as s:
        snap = compute_snapshot(s, w.admin_actor, NOV, as_of=NOW)
        prev = compute_snapshot(s, w.admin_actor, NOV.previous(), as_of=NOW)
        alerts = operational_alerts(s, w.admin_actor, NOV, now=NOW)
    return build_facts(snap, prev, alerts)


def test_template_insights_pass_their_own_grounding_check(file_engine):
    w = confirmed_world(file_engine)
    _operations(file_engine, w)
    facts = _facts(file_engine, w)
    summary = template_summary(facts)
    assert check_summary(summary, facts) == []
    assert summary.observations and summary.recommendations
    assert any("too few for reliable trends" in g for g in summary.data_gaps)  # only 1 booking
    assert not any(t["sufficient_data"] for t in facts["trends"])


def test_grounding_rejects_fabrication(file_engine):
    w = confirmed_world(file_engine)
    _operations(file_engine, w)
    facts = _facts(file_engine, w)
    bad = InsightSummary(
        headline="Strong month",
        observations=[InsightItem(text="Bookings increased 40% because of the new building gym.", metric_keys=["total_bookings"]),
                      InsightItem(text="Revenue reached $9,999.", metric_keys=["gross_booking_value"]),
                      InsightItem(text="Residents love the service.", metric_keys=["resident_happiness"])],
        recommendations=[InsightItem(text="Hire more caregivers.", metric_keys=[])],
        data_gaps=[],
    )
    problems = " | ".join(check_summary(bad, facts))
    for expected in ("number 40%", "states a cause", "number $9,999", "unknown/no metrics: ['resident_happiness']",
                     "recommendation cites unknown/no metrics: none", "trend language"):
        assert expected in problems, expected


def test_insights_agent_uses_ai_only_when_grounded(file_engine, monkeypatch):
    from pawpal import config
    monkeypatch.setattr(config, "openai_model", lambda: "gpt-test")
    w = confirmed_world(file_engine)
    _operations(file_engine, w)
    facts = _facts(file_engine, w)
    good = InsightSummary(headline="Operations overview", data_gaps=[],
                          observations=[InsightItem(text="1 visit was missed in the period.", metric_keys=["missed_visits"])],
                          recommendations=[InsightItem(text="Follow up on the missed visit with the caregiver.",
                                                       metric_keys=["missed_visits"])])
    with session_scope(file_engine) as s:
        client = FakeOpenAI(parsed=good)
        res = generate_insights(s, w.admin_actor, NOV, client=client, as_of=NOW)
        assert res.source == "ai" and res.summary.observations[0].text.startswith("1 visit")
        sent = client.calls[0]["messages"][1]["content"]
        assert "SECRET-MEDICAL-NOTE" not in sent and "Ada" not in sent and "@example.test" not in sent
        bad = good.model_copy(update={"headline": "Revenue doubled due to marketing."})
        res = generate_insights(s, w.admin_actor, NOV, client=FakeOpenAI(parsed=bad), as_of=NOW)
        assert res.source == "template_fallback" and any("cause" in u for u in res.unsupported)
        res = generate_insights(s, w.admin_actor, NOV, client=FakeOpenAI(raises=RuntimeError("down")), as_of=NOW)
        assert res.source == "template_fallback"
        res = generate_insights(s, w.admin_actor, NOV, as_of=NOW)  # no key in tests
        assert res.source == "template"


# ----------------------------------------------------------- monthly report
def test_monthly_report_sections_privacy_and_exports(file_engine):
    w = confirmed_world(file_engine)
    _operations(file_engine, w)
    with session_scope(file_engine) as s:
        rep = build_monthly_report(s, w.admin_actor, month_filter(2026, 11, data_source="real"), as_of=NOW)
        md = rep.markdown
        for n, title in enumerate(["Executive summary", "Booking performance", "Revenue overview", "Caregiver utilization",
                                   "Service completion", "Customer experience", "AI agent evaluation", "Operational risks",
                                   "Recommended actions"], 1):
            assert f"## {n}. {title}" in md
        resident = s.get(Resident, w.resident)
        md_bytes = export(s, w.admin_actor, rep, "md")
        csv_bytes = export(s, w.admin_actor, rep, "csv")
        for blob in (md_bytes, csv_bytes):
            text = blob.decode()
            assert resident.full_name not in text and resident.email not in text and "SECRET-MEDICAL-NOTE" not in text
        rows = list(csv.DictReader(io.StringIO(csv_bytes.decode())))
        assert len(rows) == len(KPI_DEFINITIONS) and {r["data_source"] for r in rows} == {"real"}
        if pdf_available():
            assert export(s, w.admin_actor, rep, "pdf").startswith(b"%PDF")
        reader = PlatformAdmin(full_name="Reader", email="rd2@example.test", permissions=["analytics:read"])
        s.add(reader)
        s.flush()
        with pytest.raises(AuthorizationError):
            export(s, Actor.admin(reader.id), rep, "csv")
