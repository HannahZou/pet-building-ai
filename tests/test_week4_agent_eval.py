"""Agent evaluation: benchmark integrity, scoring, runners; care-report evaluation; Week 3 extensions."""
import json
from collections import Counter
from datetime import date

from pawpal.agent_eval import booking as bench
from pawpal.agent_eval import care_reports as care_eval
from pawpal.care_logs import log_activity
from pawpal.care_reports import build_daily_facts, generate_daily_report
from pawpal.database import session_scope
from pawpal.models import ActivityType as A, CompletionStatus as C, EvaluationRun, ServiceType
from pawpal.schemas import ExtractedBooking, ExtractedTimeWindow
from pawpal.visit_execution import check_in, check_out
from tests.conftest import FakeOpenAI
from tests.week3_world import DAY1, at, confirmed_world


def test_benchmark_dataset_integrity():
    cases = bench.load_cases()
    assert len(cases) >= 100
    assert len({c["id"] for c in cases}) == len(cases)
    cats = Counter(c["category"] for c in cases)
    assert {"simple", "multiple_pets", "ambiguous_dates", "visit_times", "special_instructions", "photo_requests",
            "contradictory", "unsupported_services", "invalid_date_range"} <= set(cats)
    for c in cases:
        e = c["expected"]
        for k in ("start_date", "end_date"):
            assert e[k] is None or date.fromisoformat(e[k])
        assert e["service_type"] in (None, "feeding", "walking", "check_in", "full_care")
        if c["category"] == "multiple_pets":
            assert e["pet_name"] is None
        if c["expected_invalid_range"]:
            assert e["end_date"] < e["start_date"]


def _perfect(case):
    e = case["expected"]
    return ExtractedBooking(
        pet_name=e["pet_name"], start_date=e["start_date"], end_date=e["end_date"], visits_per_day=e["visits_per_day"],
        service_type=e["service_type"], photo_updates_required=e["photo_updates_required"],
        time_windows=[ExtractedTimeWindow(start=s, end=t) for s, t in e["time_windows"]],
        special_instructions=" ".join(e["special_instructions_keywords"]) or None,
    )


def test_scoring_perfect_and_wrong_predictions():
    cases = bench.load_cases()
    scored = [bench.score_case(c, _perfect(c)) for c in cases]
    agg = bench.aggregate(scored)
    assert agg["field_accuracy"] == 1.0 and agg["exact_match_accuracy"] == 1.0
    assert agg["missing_info_f1"] == 1.0 and agg["invalid_range_detection"] == 1.0
    case = next(c for c in cases if c["id"] == "simple-001")
    wrong = _perfect(case).model_copy(update={"start_date": date(2026, 11, 7), "service_type": None})
    s = bench.score_case(case, wrong)
    assert not s["exact_match"] and set(s["diffs"]) == {"start_date", "service_type"}
    assert s["predicted_missing"] == ["service_type"] and s["expected_missing"] == []
    empty = bench.score_case(case, None)
    assert empty["fields"]["pet_name"] is False and len(empty["predicted_missing"]) == 6
    assert bench.aggregate([])["field_accuracy"] is None  # zero denominators


def test_baseline_run_is_reproducible_and_stored(tmp_path, file_engine):
    system, predict, cfg = bench.baseline_predictor()
    r1 = bench.run(system, predict, cfg, out_dir=tmp_path, engine=file_engine)
    r2 = bench.run(system, predict, cfg, out_dir=None, store=False)
    assert r1["metrics"] == r2["metrics"]
    saved = json.loads((tmp_path / "booking_extraction_rules-baseline-v1.json").read_text())
    assert saved["dataset"]["size"] == len(bench.load_cases()) and saved["dataset"]["sha256"] == bench.dataset_sha()
    with session_scope(file_engine) as s:
        run = s.query(EvaluationRun).one()
        assert run.system == "rules-baseline-v1" and run.metrics["field_accuracy"] == r1["metrics"]["field_accuracy"]


def test_openai_runner_with_mocked_model(monkeypatch):
    from pawpal import config
    monkeypatch.setattr(config, "openai_model", lambda: "gpt-test")
    cases = bench.load_cases()[:5]
    fixed = _perfect(cases[0])
    system, predict, cfg = bench.openai_predictor(client=FakeOpenAI(parsed=fixed))
    result = bench.run(system, predict, cfg, cases=cases, out_dir=None, store=False)
    assert system == "openai:gpt-test" and result["metrics"]["cases"] == 5
    assert result["metrics"]["exact_match_accuracy"] < 1.0  # same answer for different requests
    failing = bench.run(*bench.openai_predictor(client=FakeOpenAI(raises=RuntimeError("x"))), cases=cases, out_dir=None, store=False)
    assert failing["metrics"]["prediction_errors"] == 5


def test_care_report_evaluation_template_and_adversarial(tmp_path):
    t = care_eval.run("template", out_dir=tmp_path, store=False)["metrics"]
    assert t["cases"] == 8 and t["factual_consistency"] == 1.0 and t["report_completeness"] == 1.0
    assert t["label_agreement"] == 1.0 and t["missing_activity_detection"] == 1.0
    a = care_eval.run("adversarial", out_dir=tmp_path, store=False)["metrics"]
    assert a["unsupported_claim_rate"] > 0.3 and a["ai_text_rejected_by_grounding_check"] == 8
    assert a["published_unsupported_claims"] == 0 and a["missing_activity_detection"] == 0.0
    assert a["human_correction_rate"] is None  # never estimated from synthetic cases


def test_contradictory_note_and_photo_gap_are_surfaced(file_engine):
    w = confirmed_world(file_engine)  # photos requested
    v = w.visits[DAY1][0]
    with session_scope(file_engine) as s:
        check_in(s, v, w.caregiver, now=at(DAY1, 7))
        log_activity(s, v, w.caregiver, A.FEEDING, C.COMPLETED, "Bowl untouched, did not eat")
        log_activity(s, v, w.caregiver, A.FRESH_WATER, C.COMPLETED)
        check_out(s, v, w.caregiver, now=at(DAY1, 7, 25))
    with session_scope(file_engine) as s:
        facts = build_daily_facts(s, w.booking, DAY1)
        assert any("marked completed but the caregiver note" in i for i in facts.issues)
        assert facts.visits[0].photo_requested_but_missing
        report = generate_daily_report(s, w.booking, DAY1)
        assert report.structured_output["photo_gaps"] == ["V1"] and "No photo was uploaded" in report.report_text
        assert report.needs_attention
