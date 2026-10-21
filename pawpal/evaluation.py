"""Evaluation of care reporting, computed from the database (never hard-coded).

Run:  python -m pawpal.evaluation

* Report factual consistency / unsupported claim rate - from the grounding check
  stored with each report (``evaluation.ai_candidate`` = what the model wrote,
  ``evaluation.final`` = what was stored after grounding).
* Missing activity detection - share of missed/incomplete essentials and missed
  visits that the report surfaced.
* Notification success rate - email notifications sent / (sent + failed).
* Visit completion rate - visits checked out (Completed or Needs Review with a
  check-out) / visits whose outcome is known (those plus Missed).
"""
from __future__ import annotations

from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import DailyCareReport, ExecutionStatus as E, VisitExecution
from .notifications import delivery_stats


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 3) if values else None


def compute_metrics(session: Session) -> dict[str, Any]:
    reports = session.scalars(select(DailyCareReport)).all()
    final = [r.evaluation.get("final", {}) for r in reports if r.evaluation]
    ai = [r.evaluation["ai_candidate"] for r in reports
          if r.evaluation and isinstance(r.evaluation.get("ai_candidate"), dict) and "claims_checked" in r.evaluation["ai_candidate"]]
    sources: dict[str, int] = {}
    for r in reports:
        src = (r.evaluation or {}).get("narrative_source", "unknown")
        sources[src] = sources.get(src, 0) + 1

    execs = session.scalars(select(VisitExecution)).all()
    done = sum(1 for e in execs if e.execution_status in (E.COMPLETED, E.NEEDS_REVIEW) and e.actual_check_out)
    known = done + sum(1 for e in execs if e.execution_status == E.MISSED)
    stats = delivery_stats(session)
    attempted = stats["email_sent"] + stats["email_failed"]
    return {
        "reports": len(reports),
        "narrative_sources": sources,
        "published_report_factual_consistency": _avg([f["factual_consistency"] for f in final if "factual_consistency" in f]),
        "published_report_unsupported_claim_rate": _avg([f["unsupported_claim_rate"] for f in final if "unsupported_claim_rate" in f]),
        "published_report_missing_activity_detection": _avg([f["missing_activity_detection"] for f in final if "missing_activity_detection" in f]),
        "ai_reports_evaluated": len(ai),
        "ai_factual_consistency": _avg([a["factual_consistency"] for a in ai]),
        "ai_unsupported_claim_rate": _avg([a["unsupported_claim_rate"] for a in ai]),
        "ai_missing_activity_detection": _avg([a["missing_activity_detection"] for a in ai]),
        "notifications": stats,
        "email_notification_success_rate": round(stats["email_sent"] / attempted, 3) if attempted else None,
        "visits_with_known_outcome": known,
        "visit_completion_rate": round(done / known, 3) if known else None,
    }


def main() -> None:
    from .database import init_db, session_scope

    init_db()
    with session_scope() as s:
        metrics = compute_metrics(s)
    width = max(len(k) for k in metrics)
    for k, v in metrics.items():
        print(f"{k:<{width}}  {v if v is not None else 'n/a (no data yet)'}")


if __name__ == "__main__":
    main()
