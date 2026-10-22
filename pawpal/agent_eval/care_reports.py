"""Care-report evaluation: synthetic visit records with hand-written expected outcomes.

    python -m pawpal.agent_eval.care_reports                      # template generator
    python -m pawpal.agent_eval.care_reports --system adversarial # scripted hallucinating stub (detector check)
    python -m pawpal.agent_eval.care_reports --system openai      # real model (needs OPENAI_API_KEY)

Each case is played through the real services (booking -> confirmation ->
check-in/out -> activity logs -> photos) in a throw-away database, then a daily
report is generated and compared with:
* the rule-based grounding check (factual consistency, unsupported claims,
  missing-activity detection) - pawpal.care_reports.evaluate_report
* the case's hand-written labels (completed / incomplete activities, issues,
  photo gaps, attention flag) -> report completeness and label agreement.

No LLM grades another LLM here. ``adversarial`` is a scripted test double that
deliberately invents facts; it is not a model and its numbers measure the
*detector*, not report quality.
"""
from __future__ import annotations

import argparse
import io
import json
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image

from ..actors import Actor
from ..availability import Block, set_capabilities, set_day_availability
from ..booking_service import create_booking_draft
from ..care_logs import log_activity
from ..care_reports import (
    AIActivity, AICareReport, AIObservation, AIPhotoRef, AIVisitSummary, build_daily_facts, generate_daily_report,
    template_report,
)
from ..database import init_db, make_engine, session_scope
from ..models import (
    ActivityType as A, Booking, Caregiver, CompletionStatus as C, PropertyManager, Resident, Pet, ServiceType as ST,
)
from ..photos import upload_photo
from ..photo_storage import LocalPhotoStorage
from ..schemas import BookingDraft
from ..scheduling import Window
from ..visit_execution import check_in, check_out, run_daily_checks
from ..workflow import confirm_booking, prepare_booking, resident_approve
from .booking import RESULTS, store_run

DAY = date(2026, 11, 6)
NY = ZoneInfo("America/New_York")
BUILDING = "Eval Tower"
DATASET_VERSION = "care_report_cases_v1"


@dataclass
class VisitSpec:
    check_in: tuple[int, int] | None
    check_out: tuple[int, int] | None = None
    logs: list[tuple] = field(default_factory=list)      # (activity, status, note, flagged)
    photos: list[str] = field(default_factory=list)      # captions


@dataclass
class Expected:
    completed: set[tuple[str, str]]
    incomplete: set[tuple[str, str]]
    issue_keywords: list[str]          # each must appear (case-insensitive) in some issue
    needs_attention: bool
    photo_gaps: list[str] = field(default_factory=list)
    must_mention: list[str] = field(default_factory=list)  # phrases the report text must contain


@dataclass
class CareCase:
    id: str
    description: str
    species: str
    service: ST
    photos_requested: bool
    visits: list[VisitSpec]
    expected: Expected


FED = (A.FEEDING, C.COMPLETED, "Ate all of the food", False)
WATER = (A.FRESH_WATER, C.COMPLETED, None, False)

# Hand-written cases and labels (reviewed manually; see docs/EVALUATION_METHODOLOGY.md).
CASES = [
    CareCase("care-001", "Fully completed day with photos", "cat", ST.FEEDING, True,
             [VisitSpec((7, 4), (7, 31), [FED, WATER], ["Luna at breakfast"]),
              VisitSpec((17, 6), (17, 30), [FED, WATER], ["Evening nap"])],
             Expected({("V1", "feeding"), ("V1", "fresh_water"), ("V2", "feeding"), ("V2", "fresh_water")}, set(), [], False)),
    CareCase("care-002", "Missing feeding record (not logged)", "cat", ST.FEEDING, False,
             [VisitSpec((7, 2), (7, 25), [WATER]), VisitSpec((17, 1), (17, 28), [FED, WATER])],
             Expected({("V1", "fresh_water"), ("V2", "feeding"), ("V2", "fresh_water")}, {("V1", "feeding")},
                      ["Feeding - not recorded"], True, must_mention=["not recorded"])),
    CareCase("care-003", "Evening visit missed", "cat", ST.FEEDING, False,
             [VisitSpec((7, 3), (7, 29), [FED, WATER]), VisitSpec(None)],
             Expected({("V1", "feeding"), ("V1", "fresh_water")}, set(), ["missed"], True, must_mention=["missed"])),
    CareCase("care-004", "Incomplete and skipped walks", "dog", ST.WALKING, False,
             [VisitSpec((7, 10), (7, 40), [(A.DOG_WALKING, C.NOT_COMPLETED, "Heavy rain; short potty break only", False), WATER]),
              VisitSpec((17, 5), (17, 25), [(A.DOG_WALKING, C.SKIPPED, "Resident asked to skip the evening walk", False), WATER])],
             Expected({("V1", "fresh_water"), ("V2", "fresh_water")}, {("V1", "dog_walking"), ("V2", "dog_walking")},
                      ["Dog walking - not completed", "Dog walking - skipped"], True)),
    CareCase("care-005", "Contradictory caregiver note", "cat", ST.FEEDING, False,
             [VisitSpec((7, 2), (7, 26), [(A.FEEDING, C.COMPLETED, "Bowl untouched, she did not eat anything", False), WATER]),
              VisitSpec((17, 0), (17, 22), [FED, WATER])],
             Expected({("V1", "feeding"), ("V1", "fresh_water"), ("V2", "feeding"), ("V2", "fresh_water")}, set(),
                      ["marked completed but the caregiver note"], True)),
    CareCase("care-006", "Photos requested but none uploaded", "cat", ST.FEEDING, True,
             [VisitSpec((7, 1), (7, 24), [FED, WATER]), VisitSpec((17, 3), (17, 27), [FED, WATER])],
             Expected({("V1", "feeding"), ("V1", "fresh_water"), ("V2", "feeding"), ("V2", "fresh_water")}, set(), [], False,
                      photo_gaps=["V1", "V2"], must_mention=["No photo was uploaded"])),
    CareCase("care-007", "Checked in but never checked out", "cat", ST.FEEDING, False,
             [VisitSpec((7, 5), None, [FED]), VisitSpec((17, 2), (17, 26), [FED, WATER])],
             Expected({("V1", "feeding"), ("V2", "feeding"), ("V2", "fresh_water")}, set(), ["no check-out"], True)),
    CareCase("care-008", "Photo of eating but no feeding record", "cat", ST.FEEDING, True,
             [VisitSpec((7, 2), (7, 20), [WATER], ["Luna eating breakfast"]), VisitSpec((17, 4), (17, 28), [FED, WATER], ["Evening"])],
             Expected({("V1", "fresh_water"), ("V2", "feeding"), ("V2", "fresh_water")}, {("V1", "feeding")},
                      ["Feeding - not recorded"], True)),
]


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (120, 150, 200)).save(buf, "JPEG")
    return buf.getvalue()


def _play_case(engine, case: CareCase, n: int, storage: LocalPhotoStorage) -> int:
    with session_scope(engine) as s:
        r = Resident(full_name=f"Eval Resident {n}", email=f"eval{n}@example.test", building_name=BUILDING,
                     apartment_number=f"{n}A", is_synthetic=True)
        s.add(r)
        s.flush()
        pet = Pet(resident_id=r.id, name="Luna" if case.species == "cat" else "Rex", species=case.species)
        cg = Caregiver(full_name=f"Eval Carer {n}", email=f"evalcg{n}@example.test", active_status=True)
        s.add_all([pet, cg])
        s.flush()
        admin = Actor("property_manager", None)
        set_capabilities(s, cg.id, admin, [case.species], list(ST), [BUILDING])
        set_day_availability(s, cg.id, DAY, [Block(time(6), time(22))], admin)
        if s.query(PropertyManager).first() is None:
            s.add(PropertyManager(full_name="Eval Manager", email="evalpm@example.test"))
        b = create_booking_draft(s, r.id, pet.id, BookingDraft(start_date=DAY, end_date=DAY, visits_per_day=len(case.visits),
                                                                service_type=case.service,
                                                                photo_updates_required=case.photos_requested),
                                 today=DAY - timedelta(days=5))
        bid, resident = b.id, Actor.resident(r.id)
        windows = [Window(time(7), time(10)), Window(time(17), time(20))][:len(case.visits)]
        prepare_booking(s, bid, resident, windows=windows)
        resident_approve(s, bid, resident)
        mid = s.query(PropertyManager).first().id
    assert confirm_booking(bid, Actor.manager(mid), engine=engine).ok
    with session_scope(engine) as s:
        visits = [(v.id, v.caregiver_id) for v in s.get(Booking, bid).visits]
    for (vid, cg_id), spec in zip(visits, case.visits):
        if spec.check_in is None:
            continue
        actor = Actor.caregiver(cg_id)
        with session_scope(engine) as s:
            check_in(s, vid, actor, now=datetime.combine(DAY, time(*spec.check_in), tzinfo=NY))
            for activity, status, note, flag in spec.logs:
                log_activity(s, vid, actor, activity, status, note, flag)
            for caption in spec.photos:
                upload_photo(s, vid, actor, "eval.jpg", _jpeg(), caption, storage=storage)
            if spec.check_out:
                check_out(s, vid, actor, now=datetime.combine(DAY, time(*spec.check_out), tzinfo=NY))
    with session_scope(engine) as s:
        run_daily_checks(s, now=datetime.combine(DAY + timedelta(days=1), time(9), tzinfo=NY))
    return bid


# ------------------------------------------------------------- adversarial stub
class AdversarialStub:
    """Scripted 'model' that claims everything went perfectly. Used only to test the detector."""

    def __init__(self):
        self.facts = None
        outer = self

        class _Completions:
            def parse(self, **kwargs):
                f = outer.facts
                refs = [v.visit_ref for v in f.visits]
                report = AICareReport(
                    pet_name=f.pet_name, report_date=f.report_date.isoformat(),
                    visit_summaries=[AIVisitSummary(visit_ref=r, summary=f"{f.pet_name} was fed and given fresh water at 9:45 AM.")
                                     for r in refs],
                    completed_activities=[AIActivity(visit_ref=r, activity=a) for r in refs for a in ("feeding", "fresh_water")],
                    incomplete_activities=[],
                    caregiver_observations=[AIObservation(visit_ref=refs[0], observation="Looks healthy and happy in the photo")],
                    photo_references=[AIPhotoRef(photo_ref="V1-P1", caption="Breakfast")],
                    issues_requiring_attention=[],
                    overall_summary=f"A perfect day: {f.pet_name} was fed, watered and walked, and is in great health.",
                )
                from types import SimpleNamespace
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=report, refusal=None))])

        from types import SimpleNamespace
        self.beta = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))


# ------------------------------------------------------------------ scoring
def _check_case(case: CareCase, report) -> dict[str, Any]:
    out = report.structured_output
    completed = {(a["visit_ref"], a["activity"]) for a in out["completed_activities"]}
    incomplete = {(a["visit_ref"], a["activity"]) for a in out["incomplete_activities"]}
    issues = " ".join(out["issues_requiring_attention"]).lower()
    exp = case.expected
    checks = {
        "every_visit_summarised": all(v["summary"] for v in out["visit_summaries"]) and len(out["visit_summaries"]) == len(case.visits),
        "completed_matches_label": completed == exp.completed,
        "incomplete_matches_label": exp.incomplete <= incomplete,
        "issues_present": all(k.lower() in issues for k in exp.issue_keywords),
        "attention_flag_matches": report.needs_attention == exp.needs_attention,
        "photo_gaps_match": out.get("photo_gaps", []) == exp.photo_gaps,
        "required_phrases": all(p.lower() in report.report_text.lower() for p in exp.must_mention),
    }
    ev = report.evaluation
    cand = ev.get("ai_candidate") if isinstance(ev.get("ai_candidate"), dict) else None
    graded = cand if cand and "claims_checked" in cand else ev["final"]
    return {
        "id": case.id, "description": case.description, "narrative_source": ev["narrative_source"],
        "checks": checks, "completeness": sum(checks.values()) / len(checks),
        "label_agreement": checks["completed_matches_label"] and checks["incomplete_matches_label"],
        "claims_checked": graded["claims_checked"], "unsupported_claims": graded["unsupported_claims"],
        "expected_missing_items": graded["expected_missing_items"], "detected_missing_items": graded["detected_missing_items"],
        "published_unsupported_claims": ev["final"]["unsupported_claims"],
    }


def run(system: str = "template", client=None, out_dir: Path | None = RESULTS, store: bool = True,
        db_engine=None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        engine = make_engine(f"sqlite:///{Path(tmp) / 'care_eval.db'}")
        init_db(engine)
        storage = LocalPhotoStorage(Path(tmp) / "photos")
        import os
        os.environ.setdefault("PAWPAL_PHOTO_DIR", str(Path(tmp) / "photos"))
        rows = []
        for n, case in enumerate(CASES, 1):
            bid = _play_case(engine, case, n, storage)
            with session_scope(engine) as s:
                if system == "template":
                    report = generate_daily_report(s, bid, DAY, use_ai=False)
                elif system == "adversarial":
                    stub = AdversarialStub()
                    stub.facts = build_daily_facts(s, bid, DAY)
                    report = generate_daily_report(s, bid, DAY, client=stub)
                else:
                    report = generate_daily_report(s, bid, DAY, client=client)
                rows.append(_check_case(case, report))
        engine.dispose()

    claims = sum(r["claims_checked"] for r in rows)
    unsupported = sum(len(r["unsupported_claims"]) for r in rows)
    expected = sum(r["expected_missing_items"] for r in rows)
    detected = sum(r["detected_missing_items"] for r in rows)
    metrics = {
        "cases": len(rows),
        "claims_checked": claims,
        "factual_consistency": round((claims - unsupported) / claims, 4) if claims else None,
        "unsupported_claim_rate": round(unsupported / claims, 4) if claims else None,
        "missing_activity_detection": round(detected / expected, 4) if expected else None,
        "report_completeness": round(sum(r["completeness"] for r in rows) / len(rows), 4),
        "label_agreement": round(sum(r["label_agreement"] for r in rows) / len(rows), 4),
        "published_unsupported_claims": sum(len(r["published_unsupported_claims"]) for r in rows),
        "ai_text_rejected_by_grounding_check": sum(r["narrative_source"] == "template_fallback" for r in rows),
        "human_correction_rate": None,
        "human_correction_note": "Measured from real manager reviews in the KPI dashboard, not from these synthetic cases.",
    }
    result = {
        "kind": "care_reports", "system": system, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": {"version": DATASET_VERSION, "size": len(CASES), "synthetic": True,
                    "labels": "hand-written per case in pawpal/agent_eval/care_reports.py"},
        "config": {"generator": system, "grounding_check": "pawpal.care_reports.evaluate_report (rule-based)"},
        "metrics": metrics, "cases": rows,
        "failures": [{"id": r["id"], "failed_checks": [k for k, v in r["checks"].items() if not v],
                      "unsupported_claims": r["unsupported_claims"][:8]} for r in rows
                     if r["completeness"] < 1 or r["unsupported_claims"]],
    }
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"care_reports_{system.replace(':', '_')}.json").write_text(json.dumps(result, indent=2, default=str))
    if store:
        store_run(result, db_engine)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate daily care reports against hand-labelled synthetic cases.")
    parser.add_argument("--system", choices=["template", "adversarial", "openai"], default="template")
    parser.add_argument("--no-db", action="store_true")
    args = parser.parse_args()
    client = None
    if args.system == "openai":
        from ..ai_extraction import get_client
        from .. import config

        client = get_client()
        system = f"openai:{config.openai_model()}"
    else:
        system = args.system
    result = run(system if args.system != "openai" else system, client=client, store=not args.no_db)
    for k, v in result["metrics"].items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
