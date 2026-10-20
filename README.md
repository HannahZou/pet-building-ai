# PawPal AI — Concierge Pet Care for Luxury Residences

In-home pet care booking and operations for luxury apartment buildings.

- **Week 1 – AI booking foundation:** residents, pets, caregivers, an AI booking-request parser, booking drafts and history.
- **Week 2 – Smart scheduling & pricing:** caregiver availability, visit generation, conflict detection, smart caregiver matching, a deterministic pricing engine, and a two-step approval workflow (resident → property manager).

Project brief for Week 2: [`docs/week2-spec.md`](docs/week2-spec.md).

```
Resident request → Visits generated → Caregivers matched → Conflicts checked → Itemised quote
   → Resident approves → Property manager approves → Final locked re-check → Confirmed
```

## Setup
```bash
python -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install -r requirements.txt
cp .env.example .env                                  # add OPENAI_API_KEY (optional)
python -m pawpal.seed_data                            # Week 1 + Week 2 demo data (safe to rerun)
streamlit run app.py
```
- The schema is managed by **Alembic migrations** and upgraded automatically on start-up.
  A Week 1 `pawpal.db` is upgraded **in place** (all rows and ids kept); a timestamped backup
  `pawpal.db.bak-…` is written first. To migrate explicitly: `python -m pawpal.migrate`.
- `python -m pawpal.seed_data --reset` drops everything and rebuilds the demo data.
- Without an API key everything still works: use **Fill in manually**, and schedule explanations
  fall back to a template.

## Tests
```bash
pytest -v
```
70 tests. Each test uses its own temporary SQLite database; OpenAI is always mocked (the key is
blanked), and `DATABASE_URL` is redirected so the real `pawpal.db` is never touched.

| Area | File |
|---|---|
| Migrations (Week 1 → Week 2 upgrade, schema = models, downgrade) | `tests/test_migrations.py` |
| Availability, time off, capabilities, permissions | `tests/test_availability.py` |
| Visit generation, time windows, matching, overlaps, buffers, alternatives | `tests/test_scheduling.py` |
| Pricing (Decimal, itemisation, versions, recalculation, no invented prices) | `tests/test_pricing.py` |
| Approval transitions, unauthorised approval, double booking (sequential, concurrent threads, DB trigger) | `tests/test_workflow.py` |
| AI explanation (mocked, no sensitive data), extraction of time windows, Week 2 seed | `tests/test_ai_and_seed_week2.py` |
| Week 1 (unchanged) | `tests/test_database.py`, `test_booking_service.py`, `test_extraction.py`, `test_seed.py` |

## The app (four portals)
Pick a portal in the sidebar. Demo mode has no login: you choose who you are, but every action is
still authorised in the service layer (e.g. a resident cannot approve someone else's booking, a
building manager cannot approve bookings in another building).

| Portal | What you can do |
|---|---|
| **Resident** | Describe a trip (AI fills the form), save a draft, choose time windows / visit length / add-ons, see the itemised quote and proposed caregivers, get a plain-language explanation, accept alternative times, approve, reject or cancel, track status. |
| **Caregiver** | Weekly or daily schedule, set hours for a day, apply a weekly pattern, mark days off, request time off, edit pet types / services / buildings. |
| **Property Manager** | Queue of bookings awaiting approval or rescheduling, conflict check (blocking vs. warning), ranked caregiver recommendations with reasons, manual reassignment (validated), approve & confirm, reject, mark completed. |
| **Scheduling & Pricing** | Visit schedule, caregiver availability grid, pricing breakdowns, unresolved conflicts, booking status counts, filterable by building and dates. |

### Demo walkthrough
1. **Resident Portal → Allison Hill → New Request** — paste example 1 below, *Generate Booking*, *Save Booking Draft*.
2. **My Bookings** — choose *Morning* and *Evening* windows, add *Medication*, *Get quote & schedule*
   → 8 visits, $320.00 quote, proposed caregivers. Click *Explain this schedule*, then *Approve*.
3. **Property Manager Portal → Olivia Grant** — open the booking, review conflicts and ranking, *Approve & confirm*.
4. Try the seeded edge cases:
   - **Teddy** (Hudson Crest, pending admin): the proposed caregiver later took time off → approving moves it to *Needs Rescheduling*; reassign the visits and the resident approves again.
   - **Louie** (Skyline Court, 22:00 window): nobody works that late → *Needs Rescheduling* with a 21:30 alternative the resident can accept.
   - **Milo** (draft): no time windows yet → the app asks; it never invents times.
   - Sign in as **Marcus Lee** (The Meridian only) and notice other buildings are not in the queue.

## Architecture
| Module | Purpose |
|---|---|
| `pawpal/models.py` | SQLAlchemy models + constraints (Week 1 tables, plus visits, availability, time off, capabilities, managers, quotes, events) |
| `pawpal/migrations/` · `pawpal/migrate.py` | Alembic migrations (`0001_week1` baseline, `0002_week2`) and the auto-upgrade runner with backup |
| `pawpal/database.py` | Engine (FKs on, WAL, explicit `BEGIN`), `session_scope`, `write_locked_session` (`BEGIN IMMEDIATE`) |
| `pawpal/availability.py` | Caregiver hours, weekly templates, time off, capabilities, visit lists |
| `pawpal/scheduling.py` | Time windows, visit generation, conflict detection, caregiver matching, alternatives — **pure deterministic Python** |
| `pawpal/pricing.py` | Configurable price book, Decimal quotes, versioned quote storage |
| `pawpal/workflow.py` | State machine, authorisation, approvals, transactional confirmation, audit trail |
| `pawpal/ai_extraction.py` | OpenAI structured extraction (Week 1, now also stated visit times/length) |
| `pawpal/ai_explain.py` | AI explanation of a schedule the engine already decided (template fallback) |
| `pawpal/reports.py` | Read-only queries for the dashboard |
| `pawpal/booking_service.py` · `schemas.py` | Week 1 validation and draft creation (unchanged behaviour) |
| `pawpal/seed_data.py` · `seed_scheduling.py` | Reproducible, idempotent demo data (Week 1 + Week 2) |
| `app.py` · `ui/` | Streamlit router and the four portals (UI only; no business rules) |

### Booking states
`Draft → Quoted → Pending Resident Approval → Pending Admin Approval → Confirmed → Completed`,
with `Needs Rescheduling` (no caregiver / conflict found), `Rejected` and `Cancelled`.
Week 1's `pending` status is migrated to `draft` (`BookingStatus.PENDING` remains as an alias).
Every change is written to `booking_events` with the actor's role and id; approvals also store
`resident_approved_at/by` and `admin_approved_at/by`.

A booking becomes **Confirmed** only when the resident approved, a manager of that building
approves, every visit has a caregiver, and the final availability/conflict check passes inside the
confirmation transaction.

### Scheduling rules
- A visit is generated for every day × visit-per-day (Nov 6–9, twice a day → 8 visits) inside the
  resident's chosen **time window**. If windows are missing the app asks for them; the AI only
  fills windows when the resident states explicit clock times.
- A caregiver qualifies if active, cares for the species, offers the service and covers the
  building. They are available if a working block contains the whole visit, they are not on
  time off, and nothing they hold overlaps once a **travel buffer** is added (10 min in the same
  building, 30 min between buildings).
- Ranking (MVP, minimise travel/cost): most visits of this booking they can cover → most visits
  already in the same building on those dates → lightest load → id. Each visit gets the best-ranked
  available caregiver at the earliest start in its window (15-minute steps).
- No caregiver → *Needs Rescheduling* with the nearest free times on the same day as alternatives
  for the resident to accept.
- The LLM never decides availability, assignments or prices; it only extracts the request and
  explains results, and never receives medical notes, special instructions or prices.

### Pricing
| Service | Price |
|---|---|
| Cat visit — 30 min | $30 |
| Dog visit — 30 min | $35 |
| Upgrade to 60 min | +$15 per visit |
| Additional pet-care service | +$10 per service per visit |

`Total = Σ visit prices + additional service fees`, computed with `Decimal`, stored in integer cents
with the price-book version (`2026-10-demo-v1`). Changing duration, add-ons or windows creates a
new quote (old ones kept, marked not current) and clears earlier approvals. Species without a
published rate (e.g. rabbits) are not priced — the resident is asked to contact the concierge.
Override rates with `PAWPAL_PRICE_BOOK=/path/prices.json` (same shape as `DEMO_PRICE_BOOK.to_dict()`).

### Double-booking protection (local MVP on SQLite)
1. **Matching** avoids anything a caregiver already holds (confirmed visits and other pending proposals).
2. **Final check**: confirmation runs in `write_locked_session`, which starts with `BEGIN IMMEDIATE`,
   so only one confirmation can run at a time; it re-validates every visit against the database.
3. **Database triggers** (`trg_visits_no_overlap_*`) abort any insert/update that would give a caregiver
   two overlapping *confirmed* visits, even if application code were wrong.
4. **Optimistic locking** (`bookings.version`) rejects writes based on a stale copy of a booking.

**Limitations:** SQLite allows one writer at a time (others wait up to 15 s); the trigger checks exact
overlap, while travel buffers are enforced in Python; times are local building time without time zones;
availability is stored per date (no recurring rules engine). WAL mode lets the Streamlit sessions read
while one writes, but this is a single-machine setup.

**Before production, move to PostgreSQL** and enforce the rule in the database:
```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;
ALTER TABLE visits ADD CONSTRAINT no_overlapping_confirmed_visits
  EXCLUDE USING gist (
    caregiver_id WITH =,
    tsrange(visit_date + start_time, visit_date + end_time) WITH &&
  ) WHERE (assignment_status = 'confirmed');
```
and confirm with `SELECT … FOR UPDATE` on the booking and caregiver rows (or `SERIALIZABLE`
isolation with retry). Add real authentication, time zones per building, and notifications.

## Example requests
1. `I'm traveling from November 6 to November 9, 2026. My cat Luna needs feeding twice a day and I'd like a photo after every visit.`
2. `Out of town Dec 20 through Dec 27, 2026. Please walk Max three times a day, no photos needed.`
3. *(stated times)* `Nov 12-14, 2026: feed Luna at 8am and between 6 and 7:30pm, an hour each visit, photos please.`
4. *(incomplete, should trigger questions)* `I'll be away next week and need someone for my dog.`
5. *(invalid range, should be blocked)* `Care from November 9 to November 6, 2026, feeding once a day, photos yes.`

## Privacy
Only the request text and pet names go to OpenAI for extraction; explanations receive first names,
dates, counts and reasons only. Medical notes and special instructions are never sent, shown in
history tables, or logged.

## Known limitations
- The Streamlit UI was smoke-tested headlessly; please click through it once with a real browser and API key.
- No authentication (demo pickers), payments or notifications yet.
