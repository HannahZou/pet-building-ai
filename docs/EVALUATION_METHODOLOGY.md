# AI evaluation methodology

PawPal has three AI components. Each is evaluated against **records or hand-written labels**, never by asking
the same model whether it was right.

| Component | What it does | How it is evaluated |
|---|---|---|
| Booking extraction agent (Week 1) | Turns a resident's message into booking fields | 103-case labelled benchmark, exact field comparison |
| Care report agent (Week 3) | Writes the daily care report narrative | Rule-based grounding check on every report + 8 hand-labelled synthetic cases |
| Business insights agent (Week 4) | Summarises verified KPIs | Rule-based grounding check on every summary (numbers, cited metrics, causal and trend language) |

## 1. Booking extraction benchmark

**Dataset** — `evaluation/datasets/booking_requests_v1.jsonl`, built by `evaluation/build_booking_benchmark.py`.
103 synthetic requests in 9 categories: simple (24), multiple pets (10), ambiguous dates (12), visit times (12:
6 without and 6 with clock times), special feeding instructions (10), photo requests (10), contradictory
instructions (8), unsupported services (8), invalid date ranges (9). Fixed "today" = 2026-10-20.

**Labels** — written by hand in the builder script (the script only expands a few hand-written phrasings with
explicit values). No LLM produced or checked any label. Labelling policy (mirrors the Week 1 extraction rules):

- a field has a value only if the request states it unambiguously; otherwise `null`;
- dates without a year resolve to the next upcoming occurrence; relative phrases ("next weekend", "this Friday")
  and numeric dates that could be day/month or month/day are `null`;
- several pets in one request → `pet_name: null` (one booking per pet; the agent should ask);
- services other than feeding / walking / check-in / full care → `service_type: null`;
- contradictory statements about a field → that field `null`;
- visit times only when clock times are stated ("morning" is not a time); a single time has `end: null`;
- special instructions are scored by required keywords, only in cases that contain instructions.

All 103 labels were reviewed line by line after writing; one weak case (a contradiction that did not affect any
extracted field) was replaced. A second reviewer should spot-check a sample before the numbers are used publicly.

**Metrics** (`pawpal/agent_eval/booking.py`)

| Metric | Definition |
|---|---|
| Field accuracy | correct fields / evaluated fields (7 fields per case + instructions where present) |
| Exact match | cases with every field correct **and** the right set of missing fields / cases |
| Pet name / date / visits per day / service type / photo / time-window accuracy | per-field correct / evaluated |
| Missing-information detection | precision, recall, F1 over the set of null fields (expected vs predicted) |
| Invalid range detection | invalid-range cases where the Week 1 validator (`review_extraction`) flags the dates |
| Prediction errors | API/parse failures (scored as empty predictions, reported separately) |

**Systems** — `openai:<model>` is the real Week 1 extractor (`pawpal.ai_extraction.extract_booking`, temperature 0).
`rules-baseline-v1` is a transparent regex extractor used as a reference and to test the pipeline without an API
key. Results go to `evaluation/results/booking_extraction_<system>.json` and the `evaluation_runs` table.

```bash
python -m pawpal.agent_eval.booking --system baseline
python -m pawpal.agent_eval.booking --system openai     # needs OPENAI_API_KEY
```

## 2. Care report evaluation

**Every report** gets a grounding check when it is generated (`pawpal.care_reports.evaluate_report`): completed
and incomplete activities, photo references and observations are matched to visit records; clock times must
appear in the records; sentences that say an activity happened must have a completed log for it; health terms
must come from a caregiver note; and every missed visit or incomplete essential task must be surfaced.
If an AI draft fails, its text is discarded and a template narrative is stored instead.

**Synthetic cases** (`pawpal/agent_eval/care_reports.py`) — 8 hand-labelled cases played through the real
services in a throw-away database: fully completed; missing feeding record; missed visit; incomplete and skipped
activities; contradictory caregiver note; photos requested but none uploaded; checked in but never checked out;
a photo captioned "eating" with no feeding record (a photo is never proof of a task).

| Metric | Definition |
|---|---|
| Factual consistency | supported claims / checked claims (AI draft for AI systems, stored report for the template) |
| Unsupported claim rate | 1 − factual consistency |
| Missing activity detection | missed visits + incomplete essentials surfaced / present in the records |
| Report completeness | passed checks / checks per case (every visit summarised, lists match labels, issues present, attention flag, photo gaps, required phrases) |
| Label agreement | cases whose completed and incomplete activity lists match the hand labels |
| Human correction rate | reports a manager rejected at least once / reviewed reports — measured on review data in the KPI dashboard, **not** estimated from synthetic cases |

Systems: `template` (deterministic generator), `adversarial` (a scripted stub that invents facts on purpose — it
measures the *detector*, not report quality), `openai:<model>` (needs a key).

```bash
python -m pawpal.agent_eval.care_reports --system template
python -m pawpal.agent_eval.care_reports --system adversarial
python -m pawpal.agent_eval.care_reports --system openai
```

**LLM-as-judge** — not used. If one is added later it must be reported as *supplemental* and never treated as
ground truth.

## 3. Business insights

Every AI summary is checked before it is shown (`pawpal.analytics.insights.check_summary`): every number must
appear in the KPI facts (rounding allowed), every observation and recommendation must cite existing metric keys,
causal language ("because", "due to", "driven by"...) is rejected because the data contains no causes, and trend
language requires a trend with at least 10 bookings in both periods. Failures fall back to the deterministic
template summary. Unit tests cover fabricated numbers, invented causes, unknown metrics and unsupported trends.

## Reproducibility

Datasets are versioned files with a SHA-256 recorded in each result. The baseline and template systems are
deterministic: re-running gives identical metrics (tested). LLM runs record the model name and temperature 0;
provider-side model updates can still change results, so keep the result files with a date.
