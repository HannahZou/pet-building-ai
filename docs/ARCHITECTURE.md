# Technical architecture

```
                 ┌──────────────────────── Streamlit (app.py, ui/) ────────────────────────┐
                 │ Resident · Caregiver · Property Manager · Scheduling & Pricing · Analytics │
                 └───────────────┬───────────────────────────────────────────────┬─────────┘
                                 │ Actor (role, id) on every call                │
          ┌──────────────────────▼───────────── service layer (pawpal/) ─────────▼──────────────┐
          │ booking_service · workflow · scheduling · pricing · availability                     │
          │ visit_execution · care_logs · photos · care_reports · notifications                  │
          │ analytics/ (data, kpis, monitoring, insights, monthly_report) · access · audit       │
          └───────┬───────────────────────────┬──────────────────────────────┬──────────────────┘
                  │ SQLAlchemy 2.0             │ OpenAI (structured outputs)   │ PhotoStorage
          ┌───────▼────────┐        ┌──────────▼──────────┐        ┌──────────▼──────────┐
          │ SQLite (WAL)   │        │ extraction · schedule│        │ LocalPhotoStorage   │
          │ Alembic 0001-4 │        │ explanation · reports│        │ (cloud-ready API)   │
          │ triggers, FKs  │        │ · insights (checked) │        └─────────────────────┘
          └────────────────┘        └──────────────────────┘
```

## Layers

| Layer | Rule |
|---|---|
| `ui/` | Rendering and input only. Reads inside a read session; writes run afterwards in their own transaction. |
| `pawpal/` services | All business rules and **all authorisation**. Every write takes an `Actor`. |
| `pawpal/analytics/` | Read-only. Every entry point resolves an access `Scope` first (`pawpal/access.py`). |
| `pawpal/agent_eval/` | Offline evaluation of the AI components; writes result files and `evaluation_runs`. |
| Database | Constraints, foreign keys and triggers back up the service rules. |

## Where AI is used — and where it is not

| Decision | Made by |
|---|---|
| Booking fields from free text | OpenAI (validated by Pydantic + deterministic checks; asks when unsure) |
| Caregiver availability, matching, conflicts | Deterministic Python |
| Prices | Deterministic price book (`Decimal`) |
| Approvals, confirmations, cancellations | Authorised people |
| Visit status, missed visits | Deterministic rules with server timestamps |
| Care report wording | OpenAI, then grounding check; lists always from records; human approval before publishing |
| KPI values, alerts | Deterministic SQL/Python |
| Management summaries | OpenAI on verified KPI facts only (no tools/SQL), then grounding check |

## Access control

| Role | Can see |
|---|---|
| Resident | own bookings, visits, photos, approved reports, notifications |
| Caregiver | own assignments, own availability; medical notes of pets they are assigned to |
| Property manager | buildings they manage (`building_name` NULL = all); booking approvals, care reports, analytics |
| Platform admin | only what `permissions` grant: `analytics:read`, `evaluation:read`, `evaluation:run`, `reports:export` |

Enforced in services (`_require_manager`, `analytics_scope`, `can_view` for photos...), tested in
`tests/test_workflow.py`, `test_care_logs_and_photos.py`, `test_week4_kpis.py` and others.

## Data integrity and concurrency (SQLite MVP)

- WAL journaling; explicit `BEGIN`; `BEGIN IMMEDIATE` for booking confirmation and check-in/out.
- Optimistic `version` columns on bookings and visit executions.
- Triggers reject overlapping confirmed visits for a caregiver.
- Notifications use an outbox + unique idempotency keys.
- Migrations back up the database file before upgrading.

## Synthetic vs real data

Seed scripts create only fictional `@example.test` residents, flagged `residents.is_synthetic`. Analytics always
filter on that flag (never combined). Real residents added through a future sign-up flow default to
`is_synthetic = false`.

## Production path

PostgreSQL with exclusion constraints and row locks; cloud object storage behind `PhotoStorage`; a job scheduler
for end-of-day checks, report drafting and email delivery; real authentication mapped onto `Actor`;
per-building time zones managed in an admin UI.
