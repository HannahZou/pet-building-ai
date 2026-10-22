# PawPal AI — Week 4: Property Analytics, AI Evaluation & Operational Intelligence

You are a senior AI engineer, data scientist, analytics engineer, and SaaS product architect.

I am developing **PawPal AI**, an AI-powered pet concierge and care operations platform for luxury residential apartment buildings.

Your task is to implement Week 4: Property Management Analytics, AI Agent Evaluation, Operational Monitoring, and Automated Business Insights.

This is the final phase of my four-week MVP development roadmap.

## Project Context

Week 1 — AI Booking Foundation
- Resident and pet profiles
- SQLAlchemy database
- Natural language booking extraction
- Booking drafts and history

Week 2 — Smart Scheduling & Pricing
- Caregiver availability
- Visit-level scheduling
- Conflict detection
- Caregiver matching
- Automated pricing
- Human approval workflow

Week 3 — Automated Pet Care Reporting
- Caregiver check-in/check-out
- Care activity logs
- Photo uploads
- AI-generated daily reports
- Human report review
- Resident notifications

Inspect the existing repository before implementation. Verify which features actually exist and which remain incomplete.

Do not assume all previous features are working.

Do not rebuild the application or overwrite working code.

# Feature 1 — Property Management Analytics Dashboard

Build an interactive property management dashboard using Streamlit and Plotly.

The dashboard should include:

### Business Performance
- Total bookings
- Confirmed bookings
- Completed bookings
- Gross booking value
- Average booking value
- Revenue by building
- Revenue by service type
- Booking conversion rate

### Operational Performance
- Total scheduled visits
- Completed visits
- Missed visits
- Visit completion rate
- Caregiver utilization rate
- Scheduling conflict rate
- Average booking processing time
- Average caregiver assignment time

### Customer Experience
- Repeat booking rate
- Resident retention rate, when sufficient longitudinal data exists
- Average care report delivery time
- Notification delivery success rate
- Customer satisfaction, only when actual feedback data exists

Requirements:
1. Calculate all KPIs from database records.
2. Do not hardcode KPI values.
3. Clearly distinguish synthetic data from real operational data.
4. Add filters for building, date range, service type, and booking status.
5. Use appropriate denominators for all metrics.
6. Handle empty datasets and missing values.
7. Avoid double counting bookings with multiple scheduled visits.
8. Prevent unauthorized cross-building data access.

# Feature 2 — KPI Definitions and Data Analytics

Create a centralized KPI calculation module.

Implement clearly defined metrics.

Examples:

Booking Conversion Rate =
Confirmed Bookings / Valid Booking Requests

Visit Completion Rate =
Completed Visits / Visits Due

Scheduling Conflict Rate =
Scheduling Attempts With Conflicts / Total Scheduling Attempts

Average Booking Processing Time =
Average(Booking Confirmation Time - Booking Creation Time)

Caregiver Utilization Rate =
Assigned Care Minutes / Available Care Minutes

AI Extraction Accuracy =
Correctly Extracted Fields / Total Evaluated Fields

Care Report Unsupported Claim Rate =
Unsupported Factual Claims / Total Evaluated Factual Claims

Define:
- Numerator
- Denominator
- Time window
- Exclusion rules
- Missing data handling

Use appropriate zero-denominator handling.

Store metric definitions in documentation.

# Feature 3 — AI Booking Agent Evaluation

Build an evaluation framework for the Week 1 natural language booking agent.

Create a reproducible benchmark dataset with at least 100 synthetic booking requests.

Include cases involving:
- Simple booking requests
- Multiple pets
- Ambiguous dates
- Missing visit times
- Special feeding instructions
- Photo update requests
- Contradictory instructions
- Unsupported services
- Invalid date ranges

Each benchmark case must contain manually defined expected structured outputs.

Evaluate:
- Pet name extraction accuracy
- Date extraction accuracy
- Visits per day accuracy
- Service type classification
- Missing information detection
- Overall field-level accuracy
- Exact-match accuracy

Do not use the same LLM to generate both benchmark labels and independently grade them without human validation.

Save evaluation results in a reproducible format.

# Feature 4 — AI Care Report Evaluation

Build an evaluation pipeline for Week 3 AI-generated pet care reports.

Compare generated reports against:
- Visit execution records
- Care activity logs
- Caregiver notes
- Photo metadata

Evaluate:
- Factual consistency
- Unsupported claim rate
- Missing activity detection
- Report completeness
- Human correction rate

Create synthetic cases containing:
- Fully completed visits
- Missing feeding records
- Missed visits
- Incomplete activities
- Contradictory caregiver notes
- Missing photo uploads

Do not assume a photo proves that a care task was completed.

For the MVP, use rule-based factual checks and a manually reviewed sample.

Any LLM-as-judge evaluation should be labeled as supplemental and not treated as ground truth.

# Feature 5 — Operational Monitoring

Create an operational monitoring module.

Track:
- Scheduling conflicts
- Unassigned visits
- Missed visits
- Late check-ins
- Failed notifications
- Reports pending approval
- Booking confirmation delays
- Repeated caregiver reassignment

Build a property manager alert panel.

Use configurable thresholds.

Do not automatically cancel bookings, change caregiver assignments, or make safety-critical decisions without authorized human approval.

# Feature 6 — Automated Business Insights Agent

Create an AI-powered analytics agent that summarizes property operations.

Example output:

"Booking demand increased this month, with most requests concentrated on weekends. Two caregivers experienced scheduling conflicts during peak periods. Consider reviewing weekend staffing availability."

Requirements:

1. Retrieve verified KPI data from the analytics service.
2. Pass structured KPI results to the LLM.
3. Generate concise operational summaries.
4. Identify trends and potential bottlenecks.
5. Clearly distinguish observations from recommendations.
6. Do not fabricate causes, trends, revenue, or performance improvements.
7. Include the reporting period and data coverage.
8. Flag insufficient data rather than inventing insights.

Do not allow the LLM to execute arbitrary SQL queries.

Use approved analytics functions and read-only database access.

# Feature 7 — Automated Reporting

Build a monthly property operations report generator.

The report should include:
- Executive summary
- Booking performance
- Revenue overview
- Caregiver utilization
- Service completion
- Customer experience
- AI agent evaluation
- Operational risks
- Recommended actions

Allow property managers to:
- Select a reporting period.
- Preview the report.
- Export the report as Markdown or CSV.
- Optionally generate PDF reports if suitable libraries are available.

Do not automatically email reports without explicit authorization.

# Feature 8 — Streamlit UI

Add the following sections:

### Executive Dashboard
- KPI cards
- Booking trends
- Revenue trends
- Building performance
- Date filters

### Care Operations
- Visit completion
- Scheduling conflicts
- Caregiver utilization
- Operational alerts

### AI Agent Evaluation
- Booking extraction accuracy
- Care report consistency
- Missing information detection
- Evaluation sample sizes
- Evaluation history

### Business Insights
- AI-generated management summaries
- Operational recommendations
- Monthly report generation

Use a clean, professional SaaS design consistent with PawPal AI branding.

# Feature 9 — Data Privacy and Access Control

Implement role-based data access for:
- Residents
- Caregivers
- Property Managers
- Platform Administrators

Requirements:
- Residents can access only their own records.
- Caregivers can access only authorized assignments.
- Property managers can access only buildings they manage.
- Platform administrators have explicitly controlled permissions.

Do not rely only on hiding Streamlit UI elements for access control.

Enforce authorization in backend service functions.

Avoid exposing private resident information in analytics exports.

Use aggregate data wherever possible.

# Feature 10 — Synthetic Data

Extend the existing synthetic dataset to support:
- Multiple residential buildings
- Historical booking trends
- Caregiver workloads
- Completed and missed visits
- Scheduling conflicts
- Care reports
- Notification delivery outcomes
- Booking processing timestamps

Use reproducible random seeds.

Create clearly labeled synthetic datasets.

Do not mix synthetic data with real operational records.

# Feature 11 — Automated Testing

Write pytest tests for:
- KPI calculations
- Zero denominators
- Date range filtering
- Booking and visit aggregation
- Revenue calculations
- Caregiver utilization
- Cross-building access restrictions
- Agent evaluation scoring
- Missing data handling
- Report generation
- Operational alert thresholds
- Preservation of Week 1–3 functionality

Mock external AI calls.

Run all tests and report actual results.

# Feature 12 — Documentation and Portfolio Readiness

Update:
- README.md
- Technical architecture documentation
- Product roadmap
- KPI definitions
- AI evaluation methodology
- Database schema documentation
- Setup instructions
- Known limitations

Create an evaluation report describing:
- Dataset size
- Test methodology
- Model configuration
- Measured performance
- Failure cases
- Limitations

Do not invent performance metrics.

Prepare the repository for public GitHub presentation.

Exclude:
- API keys
- .env files
- Private customer data
- Uploaded pet photos
- Local production databases
- Sensitive logs

# Implementation Order

1. Inspect the existing repository.
2. Identify available data and missing fields.
3. Implement KPI calculation services.
4. Build the Streamlit analytics dashboard.
5. Create booking extraction benchmarks.
6. Implement AI evaluation pipelines.
7. Add care report factual consistency evaluation.
8. Build operational monitoring.
9. Implement the business insights agent.
10. Add automated monthly reporting.
11. Implement backend authorization checks.
12. Run automated tests.
13. Fix errors.
14. Update GitHub documentation.

Preserve existing functionality throughout development.

At the end, provide:
- A summary of implemented features
- A list of changed files
- Actual test results
- Evaluation metrics calculated from real test runs
- Known limitations
- Recommended next development steps

**Start by inspecting the existing PawPal AI repository and implementing Week 4 incrementally.**