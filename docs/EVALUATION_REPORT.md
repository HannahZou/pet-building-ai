# Evaluation report — Week 4

Every number below comes from a run in this repository (result files in `evaluation/results/`). Nothing is
estimated. **No OpenAI model was run for this report** — the development environment had no API key — so there
are no LLM accuracy figures yet. Run the commands in "Next runs" to add them.

## 1. Booking extraction benchmark

| | |
|---|---|
| Dataset | `booking_requests_v1` — 103 synthetic requests, 9 categories, hand-written labels |
| System evaluated | `rules-baseline-v1` (regex reference extractor) |
| Configuration | deterministic, no model |
| Result file | `evaluation/results/booking_extraction_rules-baseline-v1.json` |

| Metric | Result |
|---|---|
| Field accuracy | 98.6% (722 / 732 fields) |
| Exact match | 94.2% (97 / 103 cases) |
| Pet name | 100% |
| Dates (start + end) | 97.1% |
| Visits per day | 100% |
| Service type | 100% |
| Photo updates | 100% |
| Visit time windows | 100% |
| Special-instruction keywords | 63.6% (7 / 11 cases) |
| Missing-information detection | precision 93.8%, recall 93.8%, F1 93.8% |
| Invalid date range caught | 100% (9 / 9) |

**Failure cases (6)**
- `ambiguous-007` "until November 9": the baseline took the only date as the start date (label: end date).
- `contradictory-003` self-correcting dates ("sorry, I mean November 7 to 10"): the baseline kept the first range
  instead of returning null.
- `special-010` "1/3 cup": the fraction looked like a numeric date, so the baseline dropped both dates.
- `special-005`, `special-007`, `photo-010`: instructions inside the same sentence as the request were not captured.

**Read this carefully.** The baseline's rules were written while looking at this benchmark, and the benchmark's
phrasing is template-based. Its score is therefore an *optimistic* figure for rules on this specific set — it is
not evidence that rules beat an LLM, and it would drop on freer phrasing. The benchmark's value is as a fixed,
labelled yardstick for the real agent.

## 2. Care report evaluation (8 hand-labelled synthetic cases)

| Metric | `template` generator | `adversarial` stub |
|---|---|---|
| Claims checked | 147 | 128 |
| Factual consistency | 100% | 47.7% |
| Unsupported claim rate | 0% | 52.3% |
| Missing activity detection | 100% | 0% |
| Report completeness (stored report) | 100% | 100% |
| Label agreement (stored report) | 100% | 100% |
| AI text rejected by the grounding check | — | 8 / 8 |
| Unsupported claims in stored reports | 0 | 0 |

The adversarial stub claims every visit went perfectly: invented feedings, a 9:45 AM time that is not in the
records, "looks healthy in the photo", a non-existent photo, and no issues. The grounding check flagged it in
all 8 cases, and because the stored lists always come from the records, none of those claims reached a stored
report. This measures the **detector**; it says nothing about how often a real model makes such errors.

## 3. Operational KPIs on the synthetic demo data

Computed by the KPI service for 2026-07-01 → 2026-09-30, all buildings, **synthetic data** (`python -m
pawpal.seed_data`, fixed seeds). These describe generated records, not real operations.

| KPI | Value | Numerator / denominator |
|---|---|---|
| Total bookings | 96 | — |
| Booking conversion rate | 82.8% | 77 / 93 |
| Gross booking value | $10,240.00 | 10 legacy bookings without quotes excluded |
| Visit completion rate | 95.9% | 307 / 320 |
| Missed visits | 13 | — |
| Scheduling conflict rate | 6.0% | 9 / 151 attempts |
| Average booking processing time | 31.9 h | 68 bookings |
| Average caregiver assignment time | 0.4 h | 75 bookings |
| Caregiver utilization | 3.2% | 9,600 / 301,500 minutes |
| Average care report delivery time | 11.4 h | 189 reports |
| Human correction rate | 9.5% | 18 / 190 reviewed reports |
| Published report unsupported claim rate | 0.0% | 0 / 2,410 claims |
| Email notification success | 95.8% | 812 / 848 |
| Customer satisfaction | Not available | no feedback data is collected |

Utilization is low because the synthetic caregivers have full-time hours and demand is light — the KPI reports
it as it is. The human correction rate reflects the seed's 10% scripted rejection rate.

## 4. Limitations

- No LLM results yet (no API key in the build environment).
- Benchmarks are synthetic and English-only; phrasing is template-based; one author wrote the labels.
- The grounding checks are rule-based (keywords, times, references). They can miss paraphrased fabrications and
  can flag unusual but correct wording; human review of reports remains required.
- Care-report cases cover one day with one pet; multi-pet days and edge cases like daylight-saving changes are
  not covered.
- Operational KPIs above are on synthetic data and say nothing about real performance.

## 5. Next runs

```bash
python -m pawpal.agent_eval.booking --system openai
python -m pawpal.agent_eval.care_reports --system openai
```
Then add the new result files and their numbers here, with the model name and date.
