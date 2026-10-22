# PawPal AI — Roadmap

## MVP (four weeks) — done

### Week 1 — AI booking foundation ✅
Residents, pets, caregivers and bookings with database-level ownership; natural-language extraction that never
invents details; editable drafts; history; synthetic data; tests.

### Week 2 — Smart scheduling & pricing ✅
Caregiver availability, time off and capabilities; visit generation from resident time windows; deterministic
matching with travel buffers; conflict detection; `Decimal` pricing with versioned quotes; resident → manager
approvals; locked confirmation and database triggers against double booking; Alembic migrations.

### Week 3 — Care reporting agent ✅
Check-in/out with server timestamps and audit trail; care checklist with explicit "not recorded" items; secure
photo uploads; AI daily reports with grounding checks and human approval; notification centre and email outbox.

### Week 4 — Analytics, AI evaluation & operational intelligence ✅
Central KPI service with documented definitions; Plotly executive / operations dashboards with filters;
103-case booking benchmark and care-report evaluation pipeline; operational alerts with configurable
thresholds; grounded business-insights agent; monthly report with Markdown/CSV/PDF export; role-based analytics
access including platform administrators; synthetic history; full documentation.

## Next

### Near term
- Run and publish the OpenAI benchmark and care-report evaluations; add a second human label reviewer
- Real authentication (residents, caregivers, managers, admins) mapped onto `Actor`
- Background scheduler for end-of-day checks, report drafting, email delivery and monthly reports
- Resident feedback after visits (enables a real satisfaction KPI)

### Platform
- PostgreSQL with exclusion constraints and row-level locks; read replica for analytics
- Cloud photo storage behind `PhotoStorage` with signed, expiring URLs
- Payments (card on file, charge on confirmation, refunds) and invoices
- SMS / push notifications with resident preferences and quiet hours

### Product
- Multi-pet bookings and bundled pricing; holiday surcharges through the price book
- Caregiver mobile experience (offline check-in, camera upload)
- Admin UI for buildings, time zones, permissions and alert thresholds
- Freer-phrasing and multilingual benchmark sets; periodic regression runs of every AI component
