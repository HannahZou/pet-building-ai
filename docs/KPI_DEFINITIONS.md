# KPI definitions

Single source of truth: `KPI_DEFINITIONS` in `pawpal/analytics/kpis.py` (this file is generated from it).

General rules

- Every KPI is computed from database records at request time; nothing is hard-coded.
- Each result carries its numerator and denominator. **Zero denominators return "No data"**, never 0% or 100%.
- Bookings are counted once by id, however many visits they have; revenue uses each booking's current quote.
- **Synthetic and real records are never combined**: a filter selects residents with `is_synthetic` true *or* false.
- Filters: building (inside the viewer's scope), date range, service type, booking status. A status filter applies to
  every metric, so rates become conditional on it.
- Booking metrics use the **service start date**; request metrics (conversion, conflicts, processing and assignment
  times) use the **request (created) date**; visit metrics use the **visit date** in the building's time zone.
- Late check-in = more than 30 minutes after the scheduled start (configurable for alerts).
- Retention needs at least 5 residents active in the previous period, otherwise "Insufficient data".
- Customer satisfaction is **not available**: PawPal does not collect feedback yet, and it is never estimated.

| KPI | Formula | Numerator | Denominator | Time window | Exclusions | Missing data |
|---|---|---|---|---|---|---|
| **Total bookings** | count(bookings) | Submitted bookings (any status except Draft) | — | Booking start date in the period | Drafts (incomplete requests) | 0 when there are none |
| **Confirmed bookings** | count(bookings in Confirmed or Completed) | Bookings now Confirmed or Completed | — | Booking start date in the period | Cancelled after confirmation, rejected | 0 when there are none |
| **Completed bookings** | count(bookings in Completed) | Bookings now Completed | — | Booking start date in the period | — | 0 when there are none |
| **Gross booking value** | Σ current quote total of confirmed + completed bookings | Current quote totals (USD) | — | Booking start date in the period | Bookings without a quote (Week 1 legacy records) - counted in the note | 0 when there are none |
| **Average booking value** | GBV / confirmed bookings with a quote | Gross booking value | Confirmed + completed bookings that have a quote | Booking start date in the period | Bookings without a quote | No data when the denominator is 0 |
| **Booking conversion rate** | Confirmed bookings / valid booking requests | Requests that reached Confirmed (now Confirmed or Completed, or with a confirmation event) | Valid requests: every booking except Drafts | Request (created) date in the period | Drafts | No data when the denominator is 0 |
| **Scheduled visits** | count(visits) | Visits of confirmed bookings | — | Visit date in the period | Cancelled visits; visits of unconfirmed bookings | 0 when there are none |
| **Completed visits** | count(due visits with a check-out) | Due visits checked out (Completed or Needs Review) | — | Visit date in the period and before today | — | 0 when there are none |
| **Missed visits** | count(due visits marked Missed) | Visits with execution status Missed | — | Visit date in the period and before today | — | 0 when there are none |
| **Visit completion rate** | Completed visits / visits due | Due visits with a check-out | Visits due: scheduled visits dated before today (local) | Visit date in the period | Today's and future visits | No data when no visit is due |
| **Caregiver utilization** | Assigned care minutes / available care minutes | Minutes of confirmed/completed visits assigned to the caregivers | Working-hour minutes of active caregivers covering the selected buildings, minus time off | Visit / work date in the period | Inactive caregivers; caregivers with no working hours | No data when no working hours are recorded |
| **Scheduling conflict rate** | Scheduling attempts with conflicts / total scheduling attempts | Attempts that ended in Needs Rescheduling | Attempts: each matcher run (Quoted → next status) and each final confirmation check | Request (created) date in the period | Bookings never quoted | No data when there are no attempts |
| **Average booking processing time** | mean(confirmation time − booking creation time) | Σ hours from creation to the Confirmed event | Bookings with a Confirmed event | Request (created) date in the period | Bookings never confirmed; legacy records without events | No data when no booking was confirmed |
| **Average caregiver assignment time** | mean(first full assignment − booking creation) | Σ hours from creation to the first Pending Resident Approval event (every visit had a caregiver) | Bookings that reached that event | Request (created) date in the period | Bookings never fully assigned | No data when none |
| **Repeat booking rate** | Residents with ≥2 confirmed bookings / residents with ≥1 | Residents with two or more confirmed/completed bookings | Residents with at least one | Booking start date in the period | — | No data when no resident booked |
| **Resident retention** | Residents active in the previous period who booked again / residents active in the previous period | Previous-period residents with a confirmed booking this period | Residents with a confirmed booking in the previous period of equal length | Current and previous period | — | Insufficient data when fewer than 5 residents were active in the previous period |
| **Average care report delivery time** | mean(report approval − last check-out of the day) | Σ hours from the day's last check-out to approval | Approved reports with at least one check-out | Report date in the period | Reports not approved; days without check-outs | No data when none |
| **Notification delivery success** | Emails sent / emails attempted | Email notifications delivered | Email notifications sent or failed (pending excluded) | Notification created in the period | In-app notifications (stored, always delivered); pending emails | No data when no email was attempted |
| **Customer satisfaction** | Average rating from resident feedback | — | — | — | — | Not available: PawPal does not collect feedback yet - never estimated |
| **Care report unsupported claim rate (published)** | Unsupported factual claims / evaluated factual claims | Claims in stored reports not supported by visit records | Claims checked by the rule-based grounding check | Report date in the period | — | No data when no claims were checked |
| **AI draft unsupported claim rate** | Unsupported claims / evaluated claims in AI drafts | Unsupported claims in what the model wrote (before fallback) | Claims checked in AI drafts | Report date in the period | Template-only reports | No data when no AI drafts exist |
| **Missing activity detection** | Surfaced missing items / missing items in records | Missed visits and incomplete essentials shown in the report | Missed visits and incomplete essentials in the records | Report date in the period | — | No data when nothing was missing |
| **Human correction rate** | Reports rejected at least once / reviewed reports | Reports a manager rejected (sent back) at least once | Reports approved or rejected | Report date in the period | Reports still pending review | No data when none were reviewed |
| **AI extraction accuracy** | Correctly extracted fields / evaluated fields | Benchmark fields matching the hand-written expected value | Fields evaluated in the latest benchmark run | Latest stored run (not period-bound) | — | No data until a benchmark run is stored |
