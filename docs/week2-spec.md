# PawPal AI — Week 2: Smart Scheduling & Pricing

You are a senior full-stack AI engineer and SaaS architect working on PawPal AI, an AI-powered pet concierge and care operations platform for luxury residential buildings.

## Project Context

Week 1 has already been implemented using Python, Streamlit, SQLAlchemy, SQLite, Pydantic, and OpenAI API.

The existing application supports resident and pet profiles, natural-language booking extraction, booking drafts, and booking history.

Your task is to extend the EXISTING project with Week 2 functionality.

**Do not rebuild the project or break existing features.**

Before coding, inspect the current repository, understand the existing architecture, and identify which files and database models need to be extended.

## Goal

Build an end-to-end scheduling and pricing workflow:

Resident Booking Request → Caregiver Availability Check → Conflict Detection → Smart Caregiver Matching → Automated Pricing → Resident Approval → Admin Approval → Confirmed Booking.

## Feature 1: Caregiver Availability

Create a caregiver availability management system.

Caregivers should be able to:

- Set available dates and working hours.
- Update their availability.
- Mark days off.
- View upcoming assigned visits.
- Specify supported pet types and services.

Extend the SQLAlchemy database with appropriate availability, time-off, and caregiver capability models.

Preserve existing records using safe migrations.

## Feature 2: Visit Scheduling

Create individual scheduled visits for every booking.

For example, a booking from November 6–9 with two visits per day should generate eight visits, assuming both dates are included.

Each visit should contain:

- Booking ID
- Caregiver ID
- Date and start/end time
- Duration
- Service type
- Assignment status

If the resident does not specify exact visit times, request preferred time windows instead of inventing them.

## Feature 3: Smart Scheduling Agent

Develop a scheduling engine that automatically recommends suitable caregivers based on:

1. Caregiver availability
2. Existing bookings
3. Time conflicts
4. Pet type and service qualifications
5. Building location
6. Travel time buffers
7. Requested visit time windows

The system must prevent overlapping assignments and flag unavailable caregivers.

Use deterministic Python logic for conflict detection and caregiver matching.

The AI agent can interpret resident requests and explain scheduling recommendations, but actual availability and assignment decisions must be validated against the database.

For the MVP, prioritize caregivers who can handle more visits within the same building to reduce travel and operating costs.

If no suitable caregiver is available, display an explanation and suggest alternative scheduling options for resident approval.

## Feature 4: Automated Pricing Engine

Create a configurable pricing engine with these initial demo rates:

| Service                     | Price          |
| --------------------------- | -------------- |
| Cat visit — 30 minutes      | $30            |
| Dog visit — 30 minutes      | $35            |
| Upgrade to 60 minutes       | +$15           |
| Additional pet care service | +$10 per visit |

Calculate:

Total Price = Sum of Visit Prices + Additional Service Fees

Requirements:

- Use Python Decimal for monetary calculations.
- Generate itemized price breakdowns.
- Store the quote and pricing version.
- Recalculate quotes when service requirements change.
- Never allow the LLM to invent prices.

## Feature 5: Booking Approval Workflow

Implement the following booking states:

- Draft
- Quoted
- Pending Resident Approval
- Pending Admin Approval
- Confirmed
- Completed
- Cancelled
- Rejected
- Needs Rescheduling

Residents must approve the quoted price and proposed schedule.

Property managers must approve caregiver assignments.

A booking can only become Confirmed after:

- Resident approval
- Admin approval
- Every visit has an assigned caregiver
- Final availability and conflict checks pass

Use database transactions to prevent double booking, including concurrent confirmation attempts.

Record approval timestamps and actor IDs.

## Feature 6: Streamlit Dashboard

Extend the existing Streamlit app with four sections.

### Resident Portal

- Review AI-generated booking requests.
- Select preferred visit time windows.
- See itemized quotes.
- Review proposed caregiver schedules.
- Approve or reject bookings.
- Track booking status.

### Caregiver Portal

- Set working availability.
- Request time off.
- View assigned visits.
- See daily and weekly schedules.

### Property Manager Portal

- View all pending bookings.
- Review recommended caregivers.
- Detect scheduling conflicts.
- Approve or reject assignments.
- Manually reassign caregivers.

### Scheduling & Pricing Dashboard

- Display visit schedules.
- Show caregiver availability.
- Display pricing breakdowns.
- Highlight unresolved conflicts.
- Show booking status.

Use a modern, clean, premium UI consistent with PawPal AI branding.

## Feature 7: Synthetic Test Data

Generate realistic synthetic data for:

- Caregiver availability
- Caregiver time off
- Existing assigned visits
- Different service qualifications
- Scheduling conflicts
- Pending approvals

Ensure seed scripts are reproducible and do not duplicate existing records.

## Feature 8: Testing

Use pytest to test:

- Caregiver availability
- Overlapping visits
- Time-off restrictions
- Service compatibility
- Visit generation
- Missing time windows
- Pricing calculations
- Booking approval transitions
- Unauthorized booking approval
- Double-booking prevention
- Existing Week 1 functionality

Use a separate test database and mock external AI calls.

## Technical Requirements

Continue using:

- Python
- Streamlit
- SQLAlchemy
- SQLite
- Pydantic
- OpenAI API

Add libraries only when necessary.

Use database migrations instead of deleting or recreating existing tables.

Keep scheduling logic, pricing logic, database operations, and UI code in separate modules.

Important: SQLite has limited concurrency controls. Implement a safe transactional confirmation strategy for the local MVP, document its limitations, and recommend PostgreSQL with database-level locking or exclusion constraints before production deployment.

## Development Instructions

Follow these steps:

1. Inspect the existing repository.
2. Explain the planned architecture and changes.
3. Implement database migrations.
4. Build caregiver availability management.
5. Implement visit generation and conflict detection.
6. Build caregiver matching.
7. Implement the pricing engine.
8. Add resident and admin approval workflows.
9. Integrate all features into Streamlit.
10. Generate synthetic data.
11. Run automated tests.
12. Fix errors and update README.md.

Do not modify working Week 1 functionality unnecessarily.

Do not claim any feature is complete without testing it.

## Expected Final Result

A working PawPal AI application where:

1. A resident submits a pet care booking request.
2. The system generates the required visits.
3. Available caregivers are recommended.
4. Scheduling conflicts are checked.
5. A transparent price quote is generated.
6. The resident reviews and approves the booking.
7. A property manager approves the caregiver assignments.
8. The booking becomes confirmed only after all validations pass.

Start by inspecting my existing project and implementing Week 2 incrementally.
