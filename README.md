# PawPal AI — Week 1: AI Booking Foundation

In-home pet care booking for luxury apartment buildings. Week 1 covers the database,
synthetic data, an AI booking-request parser, a Streamlit app and tests.

**Week 1 guarantees:** every saved booking is `pending`, no caregiver is ever assigned,
and the UI never says a booking is confirmed.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install -r requirements.txt
cp .env.example .env                                  # add OPENAI_API_KEY
python -m pawpal.seed_data                            # 20 residents, 30 pets, 10 caregivers, 50 bookings
streamlit run app.py
```
`python -m pawpal.seed_data` is reproducible (fixed seed) and safe to rerun; add `--reset` to rebuild.
Without an API key you can still use **Fill in manually**.

## Tests
```bash
pytest -v
```
OpenAI is mocked in every test; no network or API key needed.

## Layout
| File | Purpose |
|---|---|
| `pawpal/models.py` | SQLAlchemy models + constraints |
| `pawpal/database.py` | Engine (SQLite FKs on), sessions, `init_db` |
| `pawpal/schemas.py` | Pydantic schemas (`ExtractedBooking`, `BookingDraft`) |
| `pawpal/ai_extraction.py` | OpenAI structured-output extraction |
| `pawpal/booking_service.py` | Validation, ownership check, save/list |
| `pawpal/seed_data.py` | Faker seed script |
| `app.py` | Streamlit UI |
| `tests/` | pytest suite |

## Design notes
- **Ownership** is enforced twice: in `create_booking_draft` and by a composite foreign key
  `bookings(pet_id, resident_id) -> pets(id, resident_id)`.
- **No invention:** the prompt tells the model to return `null` for anything not stated;
  code then re-validates, flags missing fields, drops pet names not present in the text,
  and blocks a pet that differs from the selected one.
- **Privacy:** only the request text and pet names go to OpenAI. Medical notes are never sent,
  shown in history, or logged; request text is never logged.

## Example requests
1. `I'm traveling from November 6 to November 9, 2026. My cat Luna needs feeding twice a day and I'd like a photo after every visit.`
2. `Out of town Dec 20 through Dec 27, 2026. Please walk Max three times a day, no photos needed.`
3. *(incomplete, should trigger questions)* `I'll be away next week and need someone for my dog.`
4. *(invalid range, should be blocked)* `Care from November 9 to November 6, 2026, feeding once a day, photos yes.`

## Known limitations
- Live OpenAI extraction and the Streamlit UI need manual verification with a real API key.
- No authentication (demo resident picker only); no caregiver assignment, payments or notifications (Weeks 2–4).
