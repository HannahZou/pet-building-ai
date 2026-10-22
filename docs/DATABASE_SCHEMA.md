# Database schema

SQLite in development (`pawpal.db`), managed by Alembic migrations in `pawpal/migrations/versions/`:
`0001_week1` → `0002_week2` → `0003_week3` → `0004_week4`. Upgrades run automatically on start-up and back up the
database file first. Generated from `pawpal/models.py`; a test (`test_migrated_schema_matches_models`) fails if the
migrations and the models ever disagree.

Conventions: money in integer cents; server timestamps in UTC; visit dates/times in the building's local time
(`building_settings.timezone`); enums stored as strings with CHECK constraints; JSON columns for structured payloads.

## Week 1

### `residents`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| full_name | VARCHAR | no |  |
| email | VARCHAR | no | unique |
| building_name | VARCHAR | no |  |
| apartment_number | VARCHAR | no |  |
| phone | VARCHAR | yes |  |
| is_synthetic | BOOLEAN | no |  |

### `pets`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| resident_id | INTEGER | no | → residents.id |
| name | VARCHAR | no |  |
| species | VARCHAR | no |  |
| breed | VARCHAR | yes |  |
| age | INTEGER | yes |  |
| feeding_instructions | TEXT | yes |  |
| medical_notes | TEXT | yes |  |

Constraints: `ck_pet_age_nonnegative`, `uq_pet_id_resident_id`

### `caregivers`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| full_name | VARCHAR | no |  |
| email | VARCHAR | no | unique |
| phone | VARCHAR | yes |  |
| active_status | BOOLEAN | no |  |

### `bookings`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| resident_id | INTEGER | no | → pets.resident_id, → residents.id |
| pet_id | INTEGER | no | → pets.id |
| caregiver_id | INTEGER | yes | → caregivers.id |
| start_date | DATE | no |  |
| end_date | DATE | no |  |
| visits_per_day | INTEGER | no |  |
| service_type | VARCHAR | no |  |
| special_instructions | TEXT | yes |  |
| photo_updates_required | BOOLEAN | no |  |
| status | VARCHAR | no |  |
| created_at | DATETIME | no |  |
| visit_duration_minutes | INTEGER | no |  |
| additional_services | JSON | no |  |
| resident_approved_at | DATETIME | yes |  |
| resident_approved_by | INTEGER | yes | → residents.id |
| admin_approved_at | DATETIME | yes |  |
| admin_approved_by | INTEGER | yes | → property_managers.id |
| version | INTEGER | no |  |

Constraints: `booking_status`, `ck_booking_date_order`, `ck_booking_visit_duration`, `ck_booking_visits_range`, `service_type`

## Week 2

### `property_managers`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| full_name | VARCHAR | no |  |
| email | VARCHAR | no | unique |
| building_name | VARCHAR | yes |  |
| can_view_medical_notes | BOOLEAN | no |  |

### `caregiver_pet_types`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| caregiver_id | INTEGER | no | → caregivers.id |
| species | VARCHAR | no |  |

Constraints: `uq_caregiver_species`

### `caregiver_services`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| caregiver_id | INTEGER | no | → caregivers.id |
| service_type | VARCHAR | no |  |

Constraints: `caregiver_service_type`, `uq_caregiver_service`

### `caregiver_buildings`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| caregiver_id | INTEGER | no | → caregivers.id |
| building_name | VARCHAR | no |  |

Constraints: `uq_caregiver_building`

### `caregiver_availability`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| caregiver_id | INTEGER | no | → caregivers.id |
| work_date | DATE | no |  |
| start_time | TIME | no |  |
| end_time | TIME | no |  |

Constraints: `ck_availability_time_order`, `uq_availability_block`

### `caregiver_time_off`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| caregiver_id | INTEGER | no | → caregivers.id |
| start_date | DATE | no |  |
| end_date | DATE | no |  |
| reason | VARCHAR | yes |  |
| created_at | DATETIME | no |  |

Constraints: `ck_time_off_date_order`

### `booking_time_windows`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| booking_id | INTEGER | no | → bookings.id |
| slot | INTEGER | no |  |
| start_time | TIME | no |  |
| end_time | TIME | no |  |

Constraints: `ck_time_window_order`, `ck_time_window_slot`, `uq_time_window_slot`

### `visits`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| booking_id | INTEGER | no | → bookings.id |
| caregiver_id | INTEGER | yes | → caregivers.id |
| visit_date | DATE | no |  |
| slot | INTEGER | no |  |
| window_start | TIME | no |  |
| window_end | TIME | no |  |
| start_time | TIME | no |  |
| end_time | TIME | no |  |
| duration_minutes | INTEGER | no |  |
| service_type | VARCHAR | no |  |
| assignment_status | VARCHAR | no |  |
| created_at | DATETIME | no |  |

Constraints: `ck_visit_duration`, `ck_visit_time_order`, `ck_visit_window_order`, `uq_visit_booking_date_slot`, `visit_service_type`, `visit_status`

### `booking_quotes`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| booking_id | INTEGER | no | → bookings.id |
| pricing_version | VARCHAR | no |  |
| currency | VARCHAR | no |  |
| visit_subtotal_cents | INTEGER | no |  |
| additional_fees_cents | INTEGER | no |  |
| total_cents | INTEGER | no |  |
| line_items | JSON | no |  |
| inputs | JSON | no |  |
| is_current | BOOLEAN | no |  |
| created_at | DATETIME | no |  |

### `booking_events`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| booking_id | INTEGER | no | → bookings.id |
| from_status | VARCHAR | yes |  |
| to_status | VARCHAR | no |  |
| actor_role | VARCHAR | no |  |
| actor_id | INTEGER | yes |  |
| note | TEXT | yes |  |
| created_at | DATETIME | no |  |

## Week 3

### `building_settings`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| building_name | VARCHAR | no | unique |
| timezone | VARCHAR | no |  |

### `visit_executions`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| scheduled_visit_id | INTEGER | no | → visits.id unique |
| caregiver_id | INTEGER | no | → caregivers.id |
| actual_check_in | DATETIME | yes |  |
| actual_check_out | DATETIME | yes |  |
| execution_status | VARCHAR | no |  |
| review_reasons | JSON | no |  |
| reviewed_at | DATETIME | yes |  |
| reviewed_by | INTEGER | yes | → property_managers.id |
| review_note | TEXT | yes |  |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |
| version | INTEGER | no |  |

Constraints: `ck_execution_checkout_after_checkin`, `execution_status`

### `care_activity_logs`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| scheduled_visit_id | INTEGER | no | → visits.id |
| caregiver_id | INTEGER | no | → caregivers.id |
| activity_type | VARCHAR | no |  |
| completion_status | VARCHAR | no |  |
| notes | TEXT | yes |  |
| flagged | BOOLEAN | no |  |
| recorded_at | DATETIME | no |  |

Constraints: `activity_type`, `completion_status`

### `pet_care_photos`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| scheduled_visit_id | INTEGER | no | → visits.id |
| caregiver_id | INTEGER | no | → caregivers.id |
| file_path | VARCHAR | no | unique |
| content_type | VARCHAR | no |  |
| size_bytes | INTEGER | no |  |
| width | INTEGER | no |  |
| height | INTEGER | no |  |
| caption | VARCHAR | yes |  |
| visibility_status | VARCHAR | no |  |
| uploaded_at | DATETIME | no |  |

Constraints: `photo_visibility`

### `daily_care_reports`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| booking_id | INTEGER | no | → bookings.id |
| pet_id | INTEGER | no | → pets.id |
| report_date | DATE | no |  |
| report_text | TEXT | no |  |
| structured_output | JSON | no |  |
| source_refs | JSON | no |  |
| evaluation | JSON | no |  |
| model_identifier | VARCHAR | no |  |
| generated_at | DATETIME | no |  |
| needs_attention | BOOLEAN | no |  |
| review_status | VARCHAR | no |  |
| reviewed_by | INTEGER | yes | → property_managers.id |
| reviewed_at | DATETIME | yes |  |
| review_notes | TEXT | yes |  |
| generation_count | INTEGER | no |  |

Constraints: `report_status`, `uq_report_booking_date`

### `notifications`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| resident_id | INTEGER | no | → residents.id |
| booking_id | INTEGER | yes | → bookings.id |
| notification_type | VARCHAR | no |  |
| channel | VARCHAR | no |  |
| message | TEXT | no |  |
| idempotency_key | VARCHAR | no | unique |
| delivery_status | VARCHAR | no |  |
| provider | VARCHAR | yes |  |
| attempts | INTEGER | no |  |
| last_error | VARCHAR | yes |  |
| created_at | DATETIME | no |  |
| sent_at | DATETIME | yes |  |
| read_at | DATETIME | yes |  |

Constraints: `delivery_status`, `notification_channel`, `notification_type`

### `audit_events`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| entity_type | VARCHAR | no |  |
| entity_id | INTEGER | no |  |
| action | VARCHAR | no |  |
| actor_role | VARCHAR | no |  |
| actor_id | INTEGER | yes |  |
| details | JSON | no |  |
| created_at | DATETIME | no |  |

## Week 4

### `platform_admins`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| full_name | VARCHAR | no |  |
| email | VARCHAR | no | unique |
| permissions | JSON | no |  |

### `evaluation_runs`

| Column | Type | Null | Key / reference |
|---|---|---|---|
| id | INTEGER | no | PK |
| kind | VARCHAR | no |  |
| system | VARCHAR | no |  |
| dataset_version | VARCHAR | no |  |
| dataset_size | INTEGER | no |  |
| metrics | JSON | no |  |
| failures | JSON | no |  |
| config | JSON | no |  |
| is_synthetic | BOOLEAN | no |  |
| notes | TEXT | yes |  |
| created_at | DATETIME | no |  |

## Triggers (SQLite)

`trg_visits_no_overlap_insert` / `trg_visits_no_overlap_update` abort any write that would give a
caregiver two overlapping *confirmed* visits (`caregiver_double_booked`). On PostgreSQL use the exclusion constraint in the README.
