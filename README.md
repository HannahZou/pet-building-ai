# PawPal AI — Concierge Pet Care for Luxury Residences

In-home pet care booking and operations for luxury apartment buildings.

- **Week 1 – AI booking foundation:** residents, pets, caregivers, an AI booking-request parser, booking drafts and history.
- **Week 2 – Smart scheduling & pricing:** caregiver availability, visit generation, conflict detection, smart caregiver matching, a deterministic pricing engine, and a two-step approval workflow (resident → property manager).
- **Week 3 – Care reporting agent:** caregiver check-in/check-out, care activity logging, secure photo uploads, AI daily care reports grounded in visit records with human review, and resident notifications.

Project briefs: [`docs/week2-spec.md`](docs/week2-spec.md) · [`docs/week3-spec.md`](docs/week3-spec.md) · Roadmap: [`docs/ROADMAP.md`](docs/ROADMAP.md)

```
Request → Visits → Caregiver matched → Quote → Resident approves → Manager approves → Confirmed
   → Caregiver checks in → Checklist + photos → Checks out → Daily report drafted (AI, grounded)
   → Manager reviews & approves → Resident notified and reads the report
```

## Setup
```bash
python -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install -r requirements.txt
cp .env.example .env                                  # add OPENAI_API_KEY (optional)
python -m pawpal.seed_data                            # Week 1-3 demo data (safe to rerun)
streamlit run app.py
```
- The schema is managed by **Alembic migrations** (`0001_week1` → `0002_week2` → `0003_week3`) and
  upgraded automatically on start-up. Existing databases are upgraded **in place** (all rows and ids
  kept) after a timestamped backup `pawpal.db.bak-…`. To migrate explicitly: `python -m pawpal.migrate`.
- `python -m pawpal.seed_data --reset` drops everything and rebuilds the demo data.
- Without an API key everything still works: **Fill in manually** for bookings, and template text for
  schedule explanations and daily reports.
- Photos are stored in `data/photos/` (git-ignored, outside any public folder).

Optional settings (`.env`): `PAWPAL_DEFAULT_TIMEZONE` (default `America/New_York`),
`PAWPAL_PHOTO_DIR`, `PAWPAL_MAX_PHOTO_MB` (default 5), `PAWPAL_EMAIL_NOTIFICATIONS` (default true),
and for real email `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `PAWPAL_EMAIL_FROM`.
Without SMTP settings a **mock email provider** is used and nothing leaves your computer.

## Tests
```bash
pytest -v
```
**107 tests** (22 Week 1 + 48 Week 2 + 37 Week 3). Each test uses its own temporary SQLite database
and photo folder; OpenAI and email are always mocked; the real `pawpal.db` is never touched.

| Area | File |
|---|---|
| Migrations (Week 1 → 2 → 3 upgrades with data preserved, backfill, schema = models, downgrade) | `tests/test_migrations.py` |
| Check-in/out: valid flow, unauthorised, duplicates, invalid transitions, missed visits, cancellation | `tests/test_visit_execution.py` |
| Activity logging, unrecorded essentials, flags; photo validation, EXIF removal, authorisation | `tests/test_care_logs_and_photos.py` |
| Daily aggregation, building time zones, missing records, **AI hallucination prevention**, report approval, evaluation metrics | `tests/test_care_reports.py` |
| Notification idempotency, retries, privacy, provider selection | `tests/test_notifications.py` |
| Week 3 synthetic data | `tests/test_seed_week3.py` |
| Week 2: availability, scheduling, pricing, workflow, AI explanation, seed | `tests/test_availability.py`, `test_scheduling.py`, `test_pricing.py`, `test_workflow.py`, `test_ai_and_seed_week2.py` |
| Week 1 (unchanged) | `tests/test_database.py`, `test_booking_service.py`, `test_extraction.py`, `test_seed.py` |

## The app (four portals)
Pick a portal in the sidebar. **Demo mode has no login:** you choose who you are from a dropdown. This
simulates identity only; every action is still authorised in the service layer (only the assigned
caregiver can check in, a resident sees only their own pets' records, a manager only their buildings).

| Portal | What you can do |
|---|---|
| **Resident** | New request (AI) · My Bookings (windows, quote, approve) · **Upcoming Visits** with live status · **Care Photos** · **Daily Reports** (approved only) · **Notifications** · History |
| **Caregiver** | **Today's Visits**: Start Visit, care checklist, notes, flags, photo upload, Complete Visit, actual duration · schedule · availability · time off · skills |
| **Property Manager** | Booking approvals · **Visit completion** overview, missed visits, end-of-day checks · **Care reports** pending review, approve/reject/regenerate, photo visibility · **Service issues** · **Notifications** status and email delivery |
| **Scheduling & Pricing** | Visit schedule, availability grid, pricing, conflicts, status |

### Demo walkthrough (Week 3)
1. **Caregiver Portal → Brittany Farmer → Today's Visits.** The seed gives Ginger (Hudson Crest) two
   visits *today* and tomorrow. *Start Visit* → tick the checklist → upload a photo → *Complete Visit*.
   Do the second visit but record nothing: it goes to *Needs Review* with "Feeding – not recorded".
   After the last visit of the day the daily report is drafted automatically.
2. **Property Manager Portal → Olivia Grant → Care reports.** Open Ginger's report: grounded text,
   factual-consistency score, sources. *Approve & publish* (or reject with a note / regenerate).
3. **Resident Portal → Monica Herrera →** *Notifications* (visit started/completed, report ready,
   care issue), *Daily Reports*, *Care Photos*, *Upcoming Visits*.
4. Seeded history (Oct 12–14, 2026): **Poppy** (all visits done, one report approved), **Oscar**
   (evening visit *missed*), **Finn** (feeding not completed + flagged observation), **Coco** (checked
   in, never checked out). In *Visit completion* click **Run end-of-day checks**: Coco moves to
   *Needs Review* — nothing is ever marked *Completed* just because time passed. Resolve issues under
   *Service issues*; send pending emails under *Notifications* (mock provider).
5. Week 2 flows (booking, quote, approvals, Teddy/Louie/Milo edge cases) work as before.

## Architecture
| Module | Purpose |
|---|---|
| `pawpal/models.py` | SQLAlchemy models + constraints (Weeks 1–3) |
| `pawpal/migrations/` · `pawpal/migrate.py` | Alembic migrations and the auto-upgrade runner with backup |
| `pawpal/database.py` | Engine (FKs on, WAL, explicit `BEGIN`), `session_scope`, `write_locked_session` |
| `pawpal/availability.py` · `scheduling.py` · `pricing.py` · `workflow.py` | Week 2 scheduling, pricing and approvals |
| `pawpal/visit_execution.py` | **Week 3** check-in/out, execution states, end-of-day checks |
| `pawpal/care_logs.py` | **Week 3** activity checklist, essential tasks, review reasons |
| `pawpal/photo_storage.py` · `photos.py` | **Week 3** validation/sanitising, storage interface (local backend), authorised access |
| `pawpal/care_reports.py` | **Week 3** daily aggregation, OpenAI structured output, grounding checks, review |
| `pawpal/notifications.py` | **Week 3** in-app + email (SMTP or mock), outbox, idempotency |
| `pawpal/evaluation.py` | **Week 3** metrics from the database (`python -m pawpal.evaluation`) |
| `pawpal/audit.py` · `timeutil.py` | Audit events; UTC timestamps and building time zones |
| `pawpal/ai_extraction.py` · `ai_explain.py` | Week 1 extraction; Week 2 schedule explanations |
| `pawpal/seed_data.py` · `seed_scheduling.py` · `seed_care.py` | Reproducible, idempotent demo data |
| `app.py` · `ui/` | Streamlit router and portals (UI only; no business rules) |

## Week 3 design

### Visit execution
`Scheduled → In Progress → Completed | Needs Review`, `Scheduled → Missed | Cancelled`.
One `visit_executions` row per confirmed visit (created when the booking is confirmed; existing
confirmed visits were backfilled by migration `0003`).
- Only the **assigned caregiver** can check in/out; only visits of **confirmed** bookings, and only on
  the visit's **local date** (building time zone).
- Timestamps are **server-generated** UTC (`utc_now()`); the UI cannot supply them. Check-out must be
  strictly after check-in (also a database `CHECK` constraint).
- Duplicate check-ins/outs are rejected by status checks, a unique row per visit and an optimistic
  `version` column; check-in/out run in a write-locked transaction.
- **End-of-day checks** (manager button or `run_daily_checks`): a past day's visit with no check-in →
  *Missed* (resident notified); checked in but no check-out → *Needs Review*. **Nothing is ever marked
  Completed because time passed.**
- Every change is written to `audit_events` (ids and statuses only).

### Activity logging
Activities: Feeding, Fresh Water, Dog Walking, Litter Box Cleaning, Playtime, Wellness Observation, Other;
statuses Completed / Not completed / Skipped (caregiver) and **Not recorded** (system). *Essential*
tasks depend on the service and species (e.g. feeding → feeding + fresh water; full care for a cat
adds litter box). At check-out every essential without a record is **stored as Not recorded** —
nothing is assumed. Not completed/skipped need a factual note. Any essential not completed or any
caregiver **flag** sends the visit to *Needs Review*.

### Photos
JPG/JPEG/PNG only; size limit `PAWPAL_MAX_PHOTO_MB`; the bytes must decode as the claimed format
(a renamed text file or a PNG called `.jpg` is rejected); pixel limit against decompression bombs.
Images are **re-encoded from pixels**, which removes EXIF (GPS, device, time) and text metadata.
Random file names, stored under `data/photos/` behind a `PhotoStorage` interface (`save/read/delete`
with opaque keys — a cloud backend only needs those three methods). Access: the booking's resident
(if visible), the assigned caregiver, and managers of the building; pages receive image bytes, never
paths. Managers can hide a photo from the resident. A photo is never treated as proof of an activity.

### AI daily care reports
1. **Facts** (`build_daily_facts`): all visits of one booking on one local date, with check-in/out,
   activity logs, caregiver notes and photo captions. Pet profile = name, species, breed, age —
   **medical notes are never sent to the model**.
2. **AI narrative**: OpenAI with a Pydantic structured-output schema (`AICareReport`), temperature 0,
   strict prompt (no invented activities, times, medical observations or photo inferences; state
   "not recorded"; never hide missed items).
3. **Grounding check** (`evaluate_report`): every completed/incomplete activity, photo reference,
   observation, clock time, activity mention ("fed", "walked"…) and health term in the AI output is
   checked against the records, and every missed item must be surfaced. **Any unsupported claim or
   omission → the AI text is discarded** and a deterministic template narrative is used
   (`template_fallback`, flagged for the reviewer).
4. The stored lists (completed/incomplete activities, observations, photos, issues) always come from
   the records. Stored with the report: text, structured output, generation time, model id, review
   status, evaluation, and source record ids.
5. **Human review**: reports start *Pending review*; a manager of the building approves (→ resident
   notified) or rejects with a note (→ regenerate). Approved reports are immutable. Reports with
   missed visits, incomplete essentials or flags are marked *needs attention*.

### Notifications
Visit started · Visit completed · Daily report available (only after approval) · Missed visit ·
Care issue (sent with an approved report that needs attention). Written in the **same transaction**
as the event (outbox), each with a unique **idempotency key** (`type:scope:channel`), so retries never
duplicate. Emails are delivered afterwards (`deliver_pending`), retried up to 3 times; sent emails are
never resent. **Privacy:** messages carry the pet's name and a time at most — no notes, observations,
medical details or addresses; emails just point to the app.

### Evaluation (`python -m pawpal.evaluation`)
Computed from stored records, never hard-coded: report factual consistency and unsupported-claim
rate (for the AI draft and for the published report), missing-activity detection, email success
rate, visit completion rate. On the seeded demo data (template narratives, no API key) the command
printed: 6 reports, published factual consistency **1.0**, unsupported claim rate **0.0**, missing-item
detection **1.0**, visit completion rate **0.889** (8 of 9 visits with a known outcome; 1 missed), AI
metrics **n/a** (no AI reports generated yet). AI metrics appear once reports are generated with a
real key; the hallucination tests use a mocked model that invents a feeding, a time, a photo and
health claims — all are detected and discarded.

## Week 2 reference
**Booking states:** `Draft → Quoted → Pending Resident Approval → Pending Admin Approval → Confirmed →
Completed`, plus `Needs Rescheduling`, `Rejected`, `Cancelled`; every change in `booking_events`.
**Scheduling:** visits per day × window; qualified caregivers (species, service, building), working
hours, time off, travel buffers (10 min same building / 30 min otherwise); ranking by coverage, then
same-building visits, then load. **Pricing:** cat $30 / dog $35 per 30 min, +$15 for 60 min, +$10 per
add-on per visit; `Decimal`, integer cents, versioned quotes. **Double booking:** matching avoids held
time; confirmation in `BEGIN IMMEDIATE`; SQLite triggers reject overlapping confirmed visits;
optimistic locking.

## Production notes
Move to **PostgreSQL** before production and enforce no-overlap in the database:
```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;
ALTER TABLE visits ADD CONSTRAINT no_overlapping_confirmed_visits
  EXCLUDE USING gist (
    caregiver_id WITH =,
    tsrange(visit_date + start_time, visit_date + end_time) WITH &&
  ) WHERE (assignment_status = 'confirmed');
```
Use `SELECT … FOR UPDATE` (or `SERIALIZABLE` with retry) for confirmations and check-ins, a cloud
object store behind `PhotoStorage` with signed URLs, a job scheduler for end-of-day checks and email
delivery, and real authentication.

## Example requests
1. `I'm traveling from November 6 to November 9, 2026. My cat Luna needs feeding twice a day and I'd like a photo after every visit.`
2. `Out of town Dec 20 through Dec 27, 2026. Please walk Max three times a day, no photos needed.`
3. *(stated times)* `Nov 12-14, 2026: feed Luna at 8am and between 6 and 7:30pm, an hour each visit, photos please.`
4. *(incomplete, should trigger questions)* `I'll be away next week and need someone for my dog.`
5. *(invalid range, should be blocked)* `Care from November 9 to November 6, 2026, feeding once a day, photos yes.`

## Privacy
OpenAI receives only: the booking request text and pet names (extraction); first names, dates, counts
and reasons (schedule explanations); and the day's visit records — times, activity statuses, caregiver
notes, photo captions, pet name/species/breed/age (care reports). Medical notes are never sent to any
model, never put in notifications or logs, and are hidden from property managers unless their account
has `can_view_medical_notes`. The assigned caregiver sees the pet's medical notes on the visit card
because they need them to give care. Audit events store ids and statuses only.

## Known limitations
- The Streamlit UI was tested headlessly (scripted runs of every portal); please click through it once
  in a real browser, and try a report with a real OpenAI key.
- **No authentication**: identity is chosen from dropdowns (clearly labelled demo mode).
- End-of-day checks and email delivery run when a manager clicks the buttons (no background scheduler).
- Grounding checks are rule-based (keywords, times, references); unusual phrasing could slip past or be
  flagged conservatively — the human review step remains required.
- EXIF removal re-encodes images (small quality loss for JPEG); HEIC and other formats are not accepted.
- Missed-visit detection uses the end of the visit's local day; there is no live "late arrival" alerting
  beyond the *Overdue* label in the manager overview.
- SMTP sending is implemented but untested against a real server here; the mock provider is the default.
- SQLite single-writer limits apply (see Production notes).
