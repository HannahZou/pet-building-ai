"""Booking-extraction benchmark: scoring and runners.

    python -m pawpal.agent_eval.booking --system baseline     # rule-based reference, no API key
    python -m pawpal.agent_eval.booking --system openai       # the Week 1 LLM extractor (needs OPENAI_API_KEY)

Labels come from ``evaluation/datasets/booking_requests_v1.jsonl`` (hand-written,
see evaluation/build_booking_benchmark.py). The model under test never grades
itself: scoring is exact comparison with those labels. Results are written to
``evaluation/results/booking_extraction_<system>.json`` and stored in the
``evaluation_runs`` table for the dashboard's history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import config
from ..booking_service import review_extraction
from ..schemas import ExtractedBooking

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "evaluation" / "datasets" / "booking_requests_v1.jsonl"
RESULTS = ROOT / "evaluation" / "results"
DATASET_VERSION = "booking_requests_v1"
SCORED = ["pet_name", "start_date", "end_date", "visits_per_day", "service_type", "photo_updates_required", "time_windows"]
MISSING_FIELDS = ["pet_name", "start_date", "end_date", "visits_per_day", "service_type", "photo_updates_required"]


def load_cases(path: Path = DATASET) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def dataset_sha(path: Path = DATASET) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(pred: ExtractedBooking | None) -> dict[str, Any]:
    if pred is None:
        return {f: None for f in SCORED} | {"time_windows": [], "special_instructions": None}
    return {
        "pet_name": pred.pet_name,
        "start_date": pred.start_date.isoformat() if pred.start_date else None,
        "end_date": pred.end_date.isoformat() if pred.end_date else None,
        "visits_per_day": pred.visits_per_day,
        "service_type": pred.service_type.value if pred.service_type else None,
        "photo_updates_required": pred.photo_updates_required,
        "time_windows": [[w.start, w.end] for w in pred.time_windows],
        "special_instructions": pred.special_instructions,
    }


def score_case(case: dict, pred_obj: ExtractedBooking | None) -> dict[str, Any]:
    exp, pred = case["expected"], normalize(pred_obj)
    fields = {}
    for f in SCORED:
        p, e = pred[f], exp[f]
        if f == "pet_name" and p and e:
            fields[f] = p.strip().lower() == e.lower()
        else:
            fields[f] = p == e
    kws = exp["special_instructions_keywords"]
    if kws:  # scored only where the request contains instructions
        text = (pred["special_instructions"] or "").lower()
        fields["special_instructions"] = all(k.lower() in text for k in kws)
    exp_missing = {f for f in MISSING_FIELDS if exp[f] is None}
    pred_missing = {f for f in MISSING_FIELDS if pred[f] is None}
    invalid_detected = None
    if case["expected_invalid_range"]:
        review = review_extraction(pred_obj or ExtractedBooking(), case["expected"]["pet_name"] or "",
                                   date.fromisoformat(case["today"]))
        invalid_detected = any("earlier than the start" in e for e in review.errors)
    return {
        "id": case["id"], "category": case["category"], "fields": fields,
        "exact_match": all(fields.values()) and exp_missing == pred_missing,
        "expected_missing": sorted(exp_missing), "predicted_missing": sorted(pred_missing),
        "invalid_detected": invalid_detected,
        "diffs": {f: {"expected": exp.get(f, exp["special_instructions_keywords"] if f == "special_instructions" else None),
                      "predicted": pred[f]} for f, ok in fields.items() if not ok},
    }


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def aggregate(scored: list[dict]) -> dict[str, Any]:
    per_field = defaultdict(lambda: [0, 0])
    for s in scored:
        for f, ok in s["fields"].items():
            per_field[f][0] += ok
            per_field[f][1] += 1
    correct = sum(v[0] for v in per_field.values())
    total = sum(v[1] for v in per_field.values())
    tp = sum(len(set(s["expected_missing"]) & set(s["predicted_missing"])) for s in scored)
    fp = sum(len(set(s["predicted_missing"]) - set(s["expected_missing"])) for s in scored)
    fn = sum(len(set(s["expected_missing"]) - set(s["predicted_missing"])) for s in scored)
    precision, recall = _rate(tp, tp + fp), _rate(tp, tp + fn)
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision and recall else (0.0 if tp == 0 and (fp or fn) else None)
    invalid = [s for s in scored if s["invalid_detected"] is not None]
    by_cat = defaultdict(list)
    for s in scored:
        by_cat[s["category"]].append(s)
    return {
        "cases": len(scored),
        "fields_evaluated": total, "fields_correct": correct, "field_accuracy": _rate(correct, total),
        "exact_match_accuracy": _rate(sum(s["exact_match"] for s in scored), len(scored)),
        "pet_name_accuracy": _rate(*per_field["pet_name"]),
        "date_accuracy": _rate(per_field["start_date"][0] + per_field["end_date"][0],
                               per_field["start_date"][1] + per_field["end_date"][1]),
        "visits_per_day_accuracy": _rate(*per_field["visits_per_day"]),
        "service_type_accuracy": _rate(*per_field["service_type"]),
        "photo_accuracy": _rate(*per_field["photo_updates_required"]),
        "time_window_accuracy": _rate(*per_field["time_windows"]),
        "special_instructions_keyword_recall": _rate(*per_field["special_instructions"]),
        "missing_info_precision": precision, "missing_info_recall": recall, "missing_info_f1": f1,
        "missing_info_case_accuracy": _rate(sum(s["expected_missing"] == s["predicted_missing"] for s in scored), len(scored)),
        "invalid_range_detection": _rate(sum(bool(s["invalid_detected"]) for s in invalid), len(invalid)),
        "per_field": {f: {"correct": v[0], "evaluated": v[1], "accuracy": _rate(*v)} for f, v in per_field.items()},
        "per_category": {c: {"cases": len(v), "exact_match": _rate(sum(x["exact_match"] for x in v), len(v)),
                             "field_accuracy": _rate(sum(sum(x["fields"].values()) for x in v),
                                                     sum(len(x["fields"]) for x in v))}
                         for c, v in sorted(by_cat.items())},
    }


# ------------------------------------------------------------------ systems
def baseline_predictor() -> tuple[str, Callable[[dict], ExtractedBooking], dict]:
    from .baseline import extract

    return "rules-baseline-v1", lambda c: extract(c["text"], c["pet_names"], date.fromisoformat(c["today"])), {}


def openai_predictor(client: Any | None = None) -> tuple[str, Callable[[dict], ExtractedBooking], dict]:
    from ..ai_extraction import extract_booking, get_client

    client = client or get_client()  # raises a clear error without OPENAI_API_KEY
    model = config.openai_model()

    def predict(c: dict) -> ExtractedBooking:
        return extract_booking(c["text"], c["pet_names"], today=date.fromisoformat(c["today"]), client=client)

    return f"openai:{model}", predict, {"model": model, "temperature": 0, "prompt": "pawpal.ai_extraction.build_system_prompt"}


def run(system: str, predict: Callable[[dict], ExtractedBooking], cfg: dict, cases: list[dict] | None = None,
        out_dir: Path | None = RESULTS, engine=None, store: bool = True) -> dict[str, Any]:
    cases = cases if cases is not None else load_cases()
    scored, errors = [], []
    for c in cases:
        try:
            pred = predict(c)
        except Exception as exc:  # an API error counts as an empty prediction, and is reported
            pred = None
            errors.append({"id": c["id"], "error": type(exc).__name__})
        scored.append(score_case(c, pred))
    metrics = aggregate(scored)
    metrics["prediction_errors"] = len(errors)
    failures = [{"id": s["id"], "category": s["category"], "diffs": s["diffs"],
                 "expected_missing": s["expected_missing"], "predicted_missing": s["predicted_missing"]}
                for s in scored if not s["exact_match"]]
    result = {
        "kind": "booking_extraction", "system": system, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": {"version": DATASET_VERSION, "path": str(DATASET.relative_to(ROOT)), "sha256": dataset_sha(),
                    "size": len(cases), "synthetic": True, "labels": "hand-written (see evaluation/build_booking_benchmark.py)"},
        "config": cfg, "metrics": metrics, "errors": errors, "failures": failures,
    }
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        safe = system.replace(":", "_").replace("/", "_")
        (out_dir / f"booking_extraction_{safe}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    if store:
        store_run(result, engine)
    return result


def store_run(result: dict, engine=None) -> None:
    from ..database import init_db, session_scope
    from ..models import EvaluationRun

    init_db(engine)
    with session_scope(engine) as s:
        s.add(EvaluationRun(kind=result["kind"], system=result["system"], dataset_version=result["dataset"]["version"],
                            dataset_size=result["dataset"]["size"], metrics=result["metrics"],
                            failures=result["failures"][:60], config=result["config"], is_synthetic=True,
                            notes=(f"dataset sha256 {result['dataset']['sha256'][:12]}" if result["dataset"].get("sha256")
                                   else result["dataset"].get("labels"))))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the booking-extraction benchmark.")
    parser.add_argument("--system", choices=["baseline", "openai"], default="baseline")
    parser.add_argument("--no-db", action="store_true", help="Do not store the run in the database")
    args = parser.parse_args()
    system, predict, cfg = baseline_predictor() if args.system == "baseline" else openai_predictor()
    result = run(system, predict, cfg, store=not args.no_db)
    m = result["metrics"]
    print(f"{system} on {m['cases']} cases: field accuracy {m['field_accuracy']}, exact match {m['exact_match_accuracy']}")
    for k in ("pet_name_accuracy", "date_accuracy", "visits_per_day_accuracy", "service_type_accuracy", "photo_accuracy",
              "time_window_accuracy", "special_instructions_keyword_recall", "missing_info_f1", "invalid_range_detection",
              "prediction_errors"):
        print(f"  {k}: {m[k]}")


if __name__ == "__main__":
    main()
