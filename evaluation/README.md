# Evaluation assets

- `datasets/booking_requests_v1.jsonl` — 103 synthetic booking requests with hand-written expected outputs
  (built by `build_booking_benchmark.py`; labelling policy in `docs/EVALUATION_METHODOLOGY.md`).
- `results/` — result files written by `python -m pawpal.agent_eval.booking` and
  `python -m pawpal.agent_eval.care_reports` (one file per system; the latest run overwrites it, and every run is
  also stored in the `evaluation_runs` table).

All data here is synthetic. Results are summarised in `docs/EVALUATION_REPORT.md`.
