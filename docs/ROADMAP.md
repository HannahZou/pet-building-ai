# PawPal AI — Roadmap

## Done

### Week 1 — AI booking foundation ✅
- Residents, pets, caregivers, bookings (SQLAlchemy, SQLite) with ownership enforced in the database
- Natural-language booking extraction (OpenAI structured outputs) that never invents details
- Editable booking drafts, booking history, Streamlit app, synthetic data, tests

### Week 2 — Smart scheduling & pricing ✅
- Caregiver availability, time off, pet-type/service/building capabilities
- Visit generation from resident time windows (asks instead of inventing times)
- Deterministic conflict detection and caregiver matching (travel buffers, same-building preference)
- Decimal pricing engine with versioned, itemised quotes
- Resident → property manager approval workflow; locked confirmation; DB triggers against double booking
- Alembic migrations with in-place upgrades and automatic backups

### Week 3 — Automated pet care reporting agent ✅
- Caregiver check-in/check-out with server timestamps, audit trail and end-of-day checks
- Care activity checklist with explicit "not recorded" essentials and manager flags
- Secure photo uploads (content validation, metadata stripping, authorised access, storage interface)
- AI daily care reports grounded in visit records, with automatic hallucination checks and template fallback
- Human review and approval before publishing; resident notification centre; mock/SMTP email with idempotent delivery
- Evaluation metrics computed from the database

## Next

### Week 4 — Accounts, payments and trust (proposed)
- Real authentication and roles (residents, caregivers, managers) replacing the demo pickers
- Payments: card on file, charge on confirmation, refunds on cancellation (Stripe test mode)
- Resident feedback/ratings per visit; caregiver quality dashboard
- Background jobs (e.g. APScheduler/Celery) for end-of-day checks, report drafting and email delivery

### Later
- PostgreSQL with exclusion constraints; cloud photo storage with signed URLs
- SMS / push notifications with per-resident preferences and quiet hours
- Per-building time zones managed in the admin UI; recurring availability rules
- Caregiver mobile view (offline-friendly check-in/out, camera upload)
- Multi-pet bookings and bundled pricing; holiday surcharges via the price book
- Observability: structured logs, metrics, alerting on missed visits
- Periodic evaluation runs of the report agent against a labelled set of visit records
