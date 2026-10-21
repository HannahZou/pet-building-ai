from datetime import timezone

import pytest

from pawpal import config
from pawpal.actors import Actor, AuthorizationError
from pawpal.care_logs import log_activity
from pawpal.care_reports import (
    AIActivity, AICareReport, AIObservation, AIPhotoRef, AIVisitSummary,
    ReportError, approve_report, build_daily_facts, evaluate_report, generate_daily_report,
    reject_report, reports_for_resident, template_report,
)
from pawpal.database import session_scope
from pawpal.evaluation import compute_metrics
from pawpal.models import (
    ActivityType as A, BuildingSetting, CompletionStatus as C, Notification, NotificationType, ReportStatus,
)
from pawpal.photos import upload_photo
from pawpal.visit_execution import check_in, check_out, run_daily_checks
from tests.conftest import FakeOpenAI
from tests.week3_world import DAY1, DAY2, at, confirmed_world, jpeg_bytes


def _good_day(engine, w):
    """Morning: fed + water + photo. Evening: fed, litter (optional), water not completed."""
    m, e = w.visits[DAY1]
    with session_scope(engine) as s:
        check_in(s, m, w.caregiver, now=at(DAY1, 7, 2))
        log_activity(s, m, w.caregiver, A.FEEDING, C.COMPLETED, "Ate all of the wet food")
        log_activity(s, m, w.caregiver, A.FRESH_WATER, C.COMPLETED)
        upload_photo(s, m, w.caregiver, "a.jpg", jpeg_bytes(), "Luna on the sofa")
    with session_scope(engine) as s:
        check_out(s, m, w.caregiver, now=at(DAY1, 7, 30))
    with session_scope(engine) as s:
        check_in(s, e, w.caregiver, now=at(DAY1, 18, 1))
        log_activity(s, e, w.caregiver, A.FEEDING, C.COMPLETED)
        log_activity(s, e, w.caregiver, A.LITTER_BOX, C.COMPLETED)
        log_activity(s, e, w.caregiver, A.FRESH_WATER, C.NOT_COMPLETED, "Water fountain unplugged; resident told")
    with session_scope(engine) as s:
        res = check_out(s, e, w.caregiver, now=at(DAY1, 18, 25))
    return res


# --------------------------------------------------------------- aggregation
def test_daily_report_aggregates_both_visits(file_engine):
    w = confirmed_world(file_engine)
    res = _good_day(file_engine, w)
    assert res.day_complete
    with session_scope(file_engine) as s:
        facts = build_daily_facts(s, w.booking, DAY1)
        assert [v.visit_ref for v in facts.visits] == ["V1", "V2"]
        assert (facts.visits[0].check_in, facts.visits[1].check_in) == ("7:02 AM", "6:01 PM")
        assert facts.visits[0].photos[0].caption == "Luna on the sofa"
        assert any("Fresh water - not completed" in i for i in facts.issues)
        report = generate_daily_report(s, w.booking, DAY1)
        out = report.structured_output
        assert len(out["visit_summaries"]) == 2 and report.needs_attention
        assert {(a["visit_ref"], a["activity"]) for a in out["completed_activities"]} == {
            ("V1", "feeding"), ("V1", "fresh_water"), ("V2", "feeding"), ("V2", "litter_box")}
        assert out["incomplete_activities"][0]["activity"] == "fresh_water"
        assert set(report.source_refs) == {"visits", "executions", "activity_logs", "photos"}
        assert len(report.source_refs["activity_logs"]) == 5 and len(report.source_refs["photos"]) == 1
        assert report.review_status == ReportStatus.PENDING_REVIEW and report.model_identifier == "template-v1"
        assert "SECRET-MEDICAL-NOTE" not in str(out) + report.report_text
        # second day is a separate report
        with pytest.raises(ReportError):
            build_daily_facts(s, w.booking, DAY1.replace(day=8))


def test_grouping_uses_building_time_zone(file_engine):
    w = confirmed_world(file_engine)
    from pawpal.timeutil import ensure_building
    with session_scope(file_engine) as s:
        ensure_building(s, "The Meridian", "America/Los_Angeles")
        assert s.query(BuildingSetting).filter(BuildingSetting.building_name == "The Meridian").one().timezone == "America/Los_Angeles"
    from zoneinfo import ZoneInfo
    la = ZoneInfo("America/Los_Angeles")
    evening = w.visits[DAY1][1]
    # 01:30 UTC on Nov 7 is still 5:30 PM on Nov 6 in Los Angeles
    utc_time = at(DAY1, 17, 30, tz=la).astimezone(timezone.utc)
    assert utc_time.date() == DAY2
    with session_scope(file_engine) as s:
        check_in(s, evening, w.caregiver, now=utc_time)
        facts = build_daily_facts(s, w.booking, DAY1)
        assert facts.timezone == "America/Los_Angeles" and facts.visits[1].check_in == "5:30 PM"


def test_missing_records_are_reported_not_hidden(file_engine):
    w = confirmed_world(file_engine)
    m, _ = w.visits[DAY1]
    with session_scope(file_engine) as s:
        check_in(s, m, w.caregiver, now=at(DAY1, 7))
        log_activity(s, m, w.caregiver, A.FRESH_WATER, C.COMPLETED)
    with session_scope(file_engine) as s:
        check_out(s, m, w.caregiver, now=at(DAY1, 7, 20))   # feeding never recorded
    with session_scope(file_engine) as s:
        run_daily_checks(s, now=at(DAY2, 8))                 # evening visit never started
    with session_scope(file_engine) as s:
        report = generate_daily_report(s, w.booking, DAY1)
        out, text = report.structured_output, report.report_text
        assert any(a["activity"] == "feeding" and a["status"] == "not_recorded" for a in out["incomplete_activities"])
        assert any("missed" in i for i in out["issues_requiring_attention"])
        assert out["visit_summaries"][1]["check_in"] == "not recorded"
        assert "Feeding - not recorded" in text and "missed" in text
        assert ("V1", "feeding") not in {(a["visit_ref"], a["activity"]) for a in out["completed_activities"]}
        assert report.needs_attention and report.evaluation["final"]["missing_activity_detection"] == 1.0


# ------------------------------------------------------- hallucination guard
def _hallucinating_report(facts):
    good = template_report(facts)
    return good.model_copy(update={
        "completed_activities": good.completed_activities + [AIActivity(visit_ref="V2", activity="fresh_water")],
        "photo_references": good.photo_references + [AIPhotoRef(photo_ref="V2-P9", caption="invented")],
        "caregiver_observations": [AIObservation(visit_ref="V1", observation="Luna seemed a little lethargic and limping")],
        "visit_summaries": [AIVisitSummary(visit_ref="V1", summary="Luna was fed at 3:15 PM and looks healthy in the photo.")],
        "overall_summary": "Luna was fed, given fresh water and walked twice today.",
    })


def test_ai_hallucinations_are_detected_and_discarded(file_engine, monkeypatch):
    monkeypatch.setattr(config, "openai_model", lambda: "gpt-test")
    w = confirmed_world(file_engine)
    _good_day(file_engine, w)
    with session_scope(file_engine) as s:
        facts = build_daily_facts(s, w.booking, DAY1)
        fake = FakeOpenAI(parsed=_hallucinating_report(facts))
        report = generate_daily_report(s, w.booking, DAY1, client=fake)
        ev = report.evaluation
        assert ev["narrative_source"] == "template_fallback"
        bad = " | ".join(ev["ai_candidate"]["unsupported_claims"])
        for expected in ("completed fresh_water at V2", "photo V2-P9", "time 3:15 PM", "health term 'healthy'",
                         "health term 'lethargic'", "observation at V1", "says dog_walking happened"):
            assert expected in bad, expected
        assert ev["ai_candidate"]["factual_consistency"] < 1.0 and ev["final"]["unsupported_claims"] == []
        stored = str(report.structured_output) + report.report_text
        assert "3:15" not in stored and "healthy" not in stored and "lethargic" not in stored
        assert "V2-P9" not in stored
        assert "grounding check" in report.review_notes
        prompt = fake.calls[0]["messages"][1]["content"]
        assert "SECRET-MEDICAL-NOTE" not in prompt and "Never infer anything from photos" in fake.calls[0]["messages"][0]["content"]


def test_ai_that_hides_a_missed_visit_is_rejected(file_engine):
    w = confirmed_world(file_engine)
    m, _ = w.visits[DAY1]
    with session_scope(file_engine) as s:
        check_in(s, m, w.caregiver, now=at(DAY1, 7))
        log_activity(s, m, w.caregiver, A.FEEDING, C.COMPLETED)
        log_activity(s, m, w.caregiver, A.FRESH_WATER, C.COMPLETED)
    with session_scope(file_engine) as s:
        check_out(s, m, w.caregiver, now=at(DAY1, 7, 25))
    with session_scope(file_engine) as s:
        run_daily_checks(s, now=at(DAY2, 8))
    with session_scope(file_engine) as s:
        facts = build_daily_facts(s, w.booking, DAY1)
        rosy = template_report(facts).model_copy(update={"issues_requiring_attention": [], "incomplete_activities": [],
                                                         "overall_summary": "A lovely day for Luna."})
        assert evaluate_report(rosy, facts)["missing_activity_detection"] == 0.0
        report = generate_daily_report(s, w.booking, DAY1, client=FakeOpenAI(parsed=rosy))
        assert report.evaluation["narrative_source"] == "template_fallback"
        assert report.structured_output["issues_requiring_attention"]  # still shown


def test_grounded_ai_report_is_used(file_engine, monkeypatch):
    monkeypatch.setattr(config, "openai_model", lambda: "gpt-test")
    w = confirmed_world(file_engine)
    _good_day(file_engine, w)
    with session_scope(file_engine) as s:
        facts = build_daily_facts(s, w.booking, DAY1)
        good = template_report(facts).model_copy(update={
            "overall_summary": "Luna had two visits today with Maya. The evening fresh water was not completed because the fountain was unplugged."})
        report = generate_daily_report(s, w.booking, DAY1, client=FakeOpenAI(parsed=good))
        assert report.evaluation["narrative_source"] == "ai" and report.model_identifier == "gpt-test"
        assert report.evaluation["ai_candidate"]["unsupported_claims"] == []
        assert "fountain was unplugged" in report.report_text


def test_ai_errors_fall_back_to_template(file_engine):
    w = confirmed_world(file_engine)
    _good_day(file_engine, w)
    with session_scope(file_engine) as s:
        report = generate_daily_report(s, w.booking, DAY1, client=FakeOpenAI(raises=RuntimeError("down")))
        assert report.evaluation["narrative_source"] == "template_fallback"
        assert report.evaluation["ai_candidate"] == {"error": "RuntimeError"}


# ------------------------------------------------------------------- review
def test_report_approval_workflow(file_engine):
    w = confirmed_world(file_engine)
    _good_day(file_engine, w)
    with session_scope(file_engine) as s:
        rid = generate_daily_report(s, w.booking, DAY1).id
        assert reports_for_resident(s, w.res_actor) == []  # not published before approval
        assert s.query(Notification).filter(
            Notification.notification_type == NotificationType.DAILY_REPORT_AVAILABLE).count() == 0
    for actor in (w.res_actor, w.caregiver, Actor.manager(w.remote)):
        with pytest.raises(AuthorizationError):
            with session_scope(file_engine) as s:
                approve_report(s, rid, actor)
    with pytest.raises(ReportError, match="explain"):
        with session_scope(file_engine) as s:
            reject_report(s, rid, w.admin_actor, "")
    with session_scope(file_engine) as s:
        reject_report(s, rid, w.admin_actor, "Please double-check the evening water note")
    with session_scope(file_engine) as s:
        report = generate_daily_report(s, w.booking, DAY1, actor=w.admin_actor)  # regenerate
        assert report.id == rid and report.generation_count == 2 and report.review_status == ReportStatus.PENDING_REVIEW
        approve_report(s, rid, w.admin_actor, "Checked with caregiver")
    with session_scope(file_engine) as s:
        assert [r.id for r in reports_for_resident(s, w.res_actor)] == [rid]
        assert reports_for_resident(s, Actor.resident(w.other_resident)) == []
        types = [n.notification_type for n in s.query(Notification).filter(Notification.booking_id == w.booking)]
        assert types.count(NotificationType.DAILY_REPORT_AVAILABLE) == 2   # in-app + email
        assert types.count(NotificationType.CARE_ISSUE) == 2               # report needs attention
    with pytest.raises(ReportError, match="already approved"):
        with session_scope(file_engine) as s:
            generate_daily_report(s, w.booking, DAY1)
    with pytest.raises(ReportError):
        with session_scope(file_engine) as s:
            approve_report(s, rid, w.admin_actor)


def test_evaluation_metrics_come_from_records(file_engine):
    w = confirmed_world(file_engine)
    _good_day(file_engine, w)
    with session_scope(file_engine) as s:
        run_daily_checks(s, now=at(DAY2.replace(day=8), 8))  # both Nov 7 visits missed
        facts = build_daily_facts(s, w.booking, DAY1)
        generate_daily_report(s, w.booking, DAY1, client=FakeOpenAI(parsed=_hallucinating_report(facts)))
        generate_daily_report(s, w.booking, DAY2)
        m = compute_metrics(s)
    assert m["reports"] == 2 and m["ai_reports_evaluated"] == 1
    assert m["ai_unsupported_claim_rate"] > 0 and m["published_report_unsupported_claim_rate"] == 0.0
    assert m["visits_with_known_outcome"] == 4 and m["visit_completion_rate"] == 0.5
    assert m["narrative_sources"] == {"template_fallback": 1, "template": 1}
