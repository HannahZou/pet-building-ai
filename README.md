# PawPal AI — Pet Concierge & Care Operations for Luxury Residences

An AI-assisted platform that lets residents of luxury apartment buildings book in-home pet care, schedules and
prices visits, records what caregivers actually do, writes grounded daily care reports, and gives property
managers analytics, AI evaluation and operational alerts.

Built in four weekly phases (Python · Streamlit · SQLAlchemy · SQLite · Pydantic · OpenAI · Plotly · pytest).

| Week | Phase | Highlights |
|---|---|---|
| 1 | AI booking foundation | Natural-language booking extraction that never invents details; ownership enforced in the database |
| 2 | Smart scheduling & pricing | Visit generation, deterministic caregiver matching, conflict detection, `Decimal` pricing, two-step approval, double-booking protection |
| 3 | Care reporting agent | Check-in/out with server timestamps, activity checklists, secure photos, AI daily reports with grounding checks + human approval, notifications |
| 4 | Analytics & AI evaluation | KPI service with traceable definitions, Plotly dashboards, 103-case booking benchmark, care-report evaluation, alerts, grounded business insights, monthly reports, role-based access |

> **All data in this repository is synthetic.** Residents, pets and caregivers are fictional (`@example.test`)
> and flagged `is_synthetic`; analytics never mix them with real records.

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install -r requirements.txt
cp .env.example .env                                  # OPENAI_API_KEY is optional
python -m pawpal.seed_data                            # demo data for Weeks 1-4 (~15 s, safe to rerun)
streamlit run app.py
```
Without an API key everything works: forms can be filled manually and AI text falls back to deterministic
templates. Optional: `pip install reportlab` for PDF export. Settings are documented in `.env.example`.

The database is created and upgraded by Alembic migrations on start-up (`0001_week1` … `0004_week4`), with an
automatic backup of an existing `pawpal.db`. `python -m pawpal.seed_data --reset` rebuilds the demo data.

## Tour (demo mode — pick who you are in the sidebar)
| Portal | Try this |
|---|---|
| **Resident** (Allison Hill) | *New Request* → paste "I'm traveling from November 6 to November 9, 2026. My cat Luna needs feeding twice a day…" → save → *My Bookings*: choose windows, get the $320 quote, approve |
| **Property Manager** (Olivia Grant) | *Booking approvals* → approve & confirm; *Care reports* → review the grounded report and approve; *Service issues* |
| **Caregiver** (Brittany Farmer) | *Today's Visits* → Start Visit → checklist → photo → Complete Visit |
| **Resident** (Monica Herrera) | *Notifications*, *Daily Reports*, *Care Photos* |
| **Analytics** (Olivia / Marcus / Avery / Sam) | Executive dashboard, care operations & alerts, AI evaluation, insights + monthly report export. Marcus sees only The Meridian; Sam (admin with `analytics:read` only) cannot open evaluations or export |

Demo mode simulates identity with dropdowns; every permission is still enforced in the backend services.

## Tests
```bash
pytest -q        # 134 passed
```
Each test uses its own temporary database and photo folder; OpenAI and email are always mocked.
Week 1: 22 · Week 2: 48 · Week 3: 37 · Week 4: 27 tests (KPI maths, zero denominators, filters, aggregation,
revenue, utilization, cross-building access, agent-evaluation scoring, alerts, insights grounding, report
generation and privacy, seeds, migrations).

## AI evaluation (actual results — see [`docs/EVALUATION_REPORT.md`](docs/EVALUATION_REPORT.md))
| Evaluation | System | Result |
|---|---|---|
| Booking extraction, 103 hand-labelled cases | rule-based **baseline** (not the LLM) | 98.6% field accuracy, 94.2% exact match — optimistic: rules written alongside the benchmark |
| Care reports, 8 hand-labelled cases | template generator | 100% factual consistency, 100% completeness |
| Care reports, 8 cases | scripted hallucinating stub | 52.3% of its claims unsupported → rejected in 8/8, 0 reached stored reports |
| Booking extraction / care reports | OpenAI | **not yet run** (no API key in the build environment) — `python -m pawpal.agent_eval.booking --system openai` |

Method: [`docs/EVALUATION_METHODOLOGY.md`](docs/EVALUATION_METHODOLOGY.md). No LLM generates or grades labels.

## How the AI is kept honest
- **Extraction** returns `null` + a question for anything not stated; Python re-validates every field.
- **Scheduling, pricing, approvals, visit status, KPIs** are deterministic code — never the LLM.
- **Care reports** are written from database facts; every claim (activities, times, photos, observations, health
  terms, omissions) is checked; failing text is discarded; a manager approves before residents see anything.
- **Business insights** see only verified KPI results (no SQL/tools); numbers must match the data, causes are
  never asserted, trends need enough data, and observations are kept separate from recommendations.
- Medical notes are never sent to a model, put in notifications, analytics or exports.

## Documentation
| Document | Contents |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Layers, where AI is (and is not) used, access control, concurrency |
| [`docs/KPI_DEFINITIONS.md`](docs/KPI_DEFINITIONS.md) | Every KPI: formula, numerator, denominator, window, exclusions, missing data |
| [`docs/EVALUATION_METHODOLOGY.md`](docs/EVALUATION_METHODOLOGY.md) · [`docs/EVALUATION_REPORT.md`](docs/EVALUATION_REPORT.md) | Datasets, labelling policy, metrics, results, failure cases |
| [`docs/DATABASE_SCHEMA.md`](docs/DATABASE_SCHEMA.md) | All 23 tables by week, constraints, triggers |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Done and next |
| `docs/week2-spec.md` · `week3-spec.md` · `week4-spec.md` | Original project briefs |

## Project layout
```
app.py                 Streamlit router            ui/                 portal pages (UI only)
pawpal/                services, models, AI         pawpal/analytics/   KPIs, alerts, insights, monthly report
pawpal/agent_eval/     benchmark scoring & runners  pawpal/migrations/  Alembic migrations
evaluation/            datasets, builder, results   tests/              pytest suite
docs/                  documentation
```

## Key commands
| Command | Purpose |
|---|---|
| `python -m pawpal.seed_data [--reset]` | Synthetic demo data (Weeks 1-4) |
| `python -m pawpal.migrate` | Upgrade the database explicitly |
| `python -m pawpal.agent_eval.booking --system baseline\|openai` | Booking benchmark |
| `python -m pawpal.agent_eval.care_reports --system template\|adversarial\|openai` | Care-report evaluation |
| `python -m pawpal.evaluation` | Week 3 report/notification metrics from the database |
| `python evaluation/build_booking_benchmark.py` | Rebuild the benchmark file from the hand-written cases |

## Known limitations
- **No authentication** — identity is chosen in the sidebar (clearly labelled demo mode).
- **No LLM evaluation results yet**; the baseline benchmark score is optimistic (see the evaluation report).
- Synthetic data only; customer satisfaction is not measured (no feedback collected).
- End-of-day checks, report drafting and email delivery run on demand (manager buttons), not on a scheduler.
- Grounding checks are rule-based; human review stays in the loop.
- SQLite single-writer limits; see production notes.
- The Streamlit UI was tested headlessly with scripted runs of every page; click through it once in a browser.

## Production notes
Move to PostgreSQL and enforce no-overlap in the database:
```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;
ALTER TABLE visits ADD CONSTRAINT no_overlapping_confirmed_visits
  EXCLUDE USING gist (caregiver_id WITH =, tsrange(visit_date + start_time, visit_date + end_time) WITH &&)
  WHERE (assignment_status = 'confirmed');
```
Add real authentication mapped onto `Actor`, a job scheduler, cloud photo storage behind `PhotoStorage` with
signed URLs, and monitoring. Never commit `.env`, databases or uploaded photos (all git-ignored).
