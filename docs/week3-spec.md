# PawPal AI — Week 3: Automated Pet Care Reporting Agent

You are a senior full-stack AI engineer, Python backend developer, and SaaS product architect.

I am developing **PawPal AI**, an AI-powered pet concierge and care operations platform designed for luxury residential apartment buildings.

Your task is to extend my existing application with Week 3 functionality: caregiver check-in/check-out, pet care activity logging, photo uploads, AI-generated care reports, and automated resident notifications.

Do NOT rebuild the project.

## Project Context

Week 1 — AI Booking Foundation:
- Resident and pet management
- Natural language booking extraction
- SQLAlchemy database
- Booking drafts and booking history

Week 2 — Smart Scheduling & Pricing:
- Caregiver availability management
- Visit-level scheduling
- Scheduling conflict detection
- Caregiver matching
- Automated pricing
- Resident and administrator approvals

Inspect the repository to determine which features are actually implemented. Do not assume all planned features exist.

## Technology Stack

Continue using the existing stack:
- Python
- Streamlit
- SQLAlchemy
- SQLite
- Pydantic
- OpenAI API
- pytest

Use local photo storage for development, with an abstraction that can later support cloud storage.

Use environment variables for all API credentials.

# Feature 1 — Caregiver Check-in / Check-out

Extend the ScheduledVisit model or create a VisitExecution model.

Store:
- id
- scheduled_visit_id
- caregiver_id
- actual_check_in
- actual_check_out
- execution_status
- created_at
- updated_at

Supported statuses:
- Scheduled
- In Progress
- Completed
- Missed
- Cancelled
- Needs Review

Requirements:

1. Only the assigned caregiver can check in.
2. Only confirmed visits can begin.
3. A caregiver cannot check out before checking in.
4. Check-out must occur after check-in.
5. Use server-generated timestamps.
6. Prevent duplicate check-ins and check-outs.
7. Record an audit trail of important state changes.
8. Do not mark a visit completed solely because the scheduled end time has passed.

Build a Streamlit caregiver interface with:
- Today's assigned visits
- Start Visit button
- Complete Visit button
- Actual service duration
- Visit status

For the local demo, clearly distinguish simulated caregiver identity from production authentication.

# Feature 2 — Pet Care Activity Logging

Create a CareActivityLog model.

Fields:
- id
- scheduled_visit_id
- caregiver_id
- activity_type
- completion_status
- notes
- recorded_at

Supported activities:
- Feeding
- Fresh Water
- Dog Walking
- Litter Box Cleaning
- Playtime
- Wellness Observation
- Other

Caregivers should be able to record which tasks were actually completed.

Important:
- Never assume an activity was completed.
- Store incomplete or skipped tasks explicitly.
- Allow caregivers to add factual observations.
- Flag missed essential care tasks for human review.

# Feature 3 — Pet Photo Uploads

Create a PetCarePhoto model.

Fields:
- id
- scheduled_visit_id
- caregiver_id
- file_path
- uploaded_at
- caption
- visibility_status

Requirements:
- Allow JPG, JPEG, and PNG uploads.
- Validate file extensions and actual file content.
- Enforce configurable file size limits.
- Generate unique filenames.
- Store photos outside the public static directory.
- Associate every photo with the correct visit.
- Restrict photo access to authorized residents, assigned caregivers, and administrators.
- Remove sensitive image metadata where practical.
- Avoid exposing local file paths in resident-facing pages.

For the MVP, use local storage.

Design a storage interface so that cloud object storage can be added later.

Do not assume that an uploaded image proves a particular care activity occurred.

# Feature 4 — AI Care Reporting Agent

Build an AI agent that automatically generates pet care summaries from actual caregiver records.

Input:
- Pet profile
- Confirmed booking
- Scheduled visits
- Actual check-in/check-out records
- Care activity logs
- Caregiver observations
- Photo captions and references

Output:
- Pet name
- Report date
- Visit summaries
- Completed activities
- Incomplete activities
- Caregiver observations
- Photo references
- Issues requiring attention
- Overall daily summary

Use OpenAI API with Pydantic structured outputs.

The report must be grounded in the database records.

The AI must NOT:
- Invent completed activities.
- Claim a pet was fed without a feeding record.
- Invent medical observations.
- Infer health conditions from photos.
- Invent visit times.
- Hide missing visits or incomplete tasks.

If information is missing, explicitly state that the information was not recorded.

If any essential task is missed or an unusual condition is reported, flag the report for human review.

Use a human approval step before publishing AI-generated reports.

Store the report text, structured output, generation timestamp, model identifier, review status, and source record references.

# Feature 5 — Daily Report Aggregation

A booking can contain multiple visits per day.

Create a reporting service that aggregates all visits for one pet on one local calendar date.

Example:

Pet: Luna
Date: November 6, 2026

Morning visit:
- 9:00 AM check-in
- Feeding completed
- Fresh water completed
- Photo uploaded

Evening visit:
- 6:00 PM check-in
- Feeding completed
- Litter cleaning completed
- Photo uploaded

The system should generate one daily care report containing both visits.

If one visit is incomplete or missing, the report must reflect that accurately.

Use the building's timezone when grouping visits by day.

# Feature 6 — Automated Resident Notifications

Implement a notification service for:
- Visit started
- Visit completed
- Daily report available
- Missed visit
- Care issue requiring attention

For development:
- Use an in-app notification center.
- Provide an optional email integration using environment variables.
- Use a mock email provider when credentials are missing.

Store:
- notification_id
- resident_id
- booking_id
- notification_type
- message
- created_at
- delivery_status

Implement idempotent delivery so retries do not create duplicate notifications.

Do not automatically send AI-generated reports before approval.

Use appropriate notification privacy protections.

# Feature 7 — Streamlit UI

Extend the existing application with three areas.

## Caregiver Portal

Add:
- Today's Visits
- Check-in / Check-out
- Care Activity Checklist
- Photo Upload
- Caregiver Notes
- Visit Completion Status

## Resident Portal

Add:
- Upcoming Visits
- Visit Status
- Care Photos
- Daily Care Reports
- Notification Center

Residents must only see records belonging to their own pets and bookings.

## Property Manager Portal

Add:
- Visit Completion Overview
- Missed Visits
- Care Reports Pending Review
- Report Approval
- Service Issues
- Notification Status

Do not display sensitive pet medical information to property managers unless their role is explicitly authorized to access it.

# Feature 8 — Database Models

Extend the database with appropriate models for:
- VisitExecution
- CareActivityLog
- PetCarePhoto
- DailyCareReport
- Notification
- AuditEvent

Use SQLAlchemy relationships and foreign key constraints.

Create safe database migrations.

Preserve all existing Week 1 and Week 2 records.

# Feature 9 — Synthetic Data

Generate synthetic examples covering:
- Completed visits
- In-progress visits
- Missed visits
- Feeding logs
- Walking logs
- Photo uploads
- Caregiver observations
- Daily reports
- Pending notifications

Use fictional resident and pet information.

Make test data reproducible and safe to rerun.

# Feature 10 — Testing and Evaluation

Write pytest tests covering:
- Valid check-in/check-out
- Unauthorized check-in
- Duplicate check-in prevention
- Invalid visit status transitions
- Activity logging
- Photo upload validation
- Photo authorization
- Daily report aggregation
- Missing care records
- AI hallucination prevention
- Report approval
- Notification idempotency
- Preservation of Week 1 and Week 2 features

Mock OpenAI API and email calls.

Evaluate AI-generated reports against their source records.

Track:
- Report factual consistency
- Unsupported claim rate
- Missing activity detection
- Notification success rate
- Visit completion rate

Do not fabricate evaluation results.

# Engineering Requirements

1. Inspect the current repository before modifying files.
2. Preserve existing application functionality.
3. Keep database, service, AI, storage, and UI logic separate.
4. Use safe database migrations.
5. Keep credentials in environment variables.
6. Never log private resident details or sensitive medical notes unnecessarily.
7. Use database transactions for important state transitions.
8. Avoid unnecessary external dependencies.
9. Add meaningful error handling.
10. Document any limitations.

# Deliverables

Provide:
1. Updated database models and migrations
2. Caregiver check-in/check-out functionality
3. Activity logging service
4. Secure photo upload functionality
5. AI care report generation service
6. Report review and approval workflow
7. Notification service
8. Updated Streamlit pages
9. Synthetic test data
10. Automated tests
11. Updated README.md
12. Updated project roadmap

# Implementation Order

First inspect the repository and summarize what is already working.

Then:

1. Design the Week 3 data models.
2. Implement safe database migrations.
3. Build visit check-in/check-out.
4. Build activity logging.
5. Implement photo storage.
6. Build daily report aggregation.
7. Integrate the OpenAI reporting agent.
8. Implement human review.
9. Add resident notifications.
10. Extend the Streamlit interface.
11. Write and run tests.
12. Fix errors.
13. Update documentation.

Do not claim that functionality is complete without verifying it.

At the end, provide a summary of implemented features, test results, and remaining limitations.

**Start implementing Week 3 in my existing PawPal AI repository now.**