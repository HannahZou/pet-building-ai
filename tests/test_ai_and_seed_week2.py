from datetime import date, time
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from pawpal.ai_explain import explain_recommendation, template_explanation
from pawpal.ai_extraction import extract_booking
from pawpal.database import session_scope
from pawpal.models import Booking, BookingStatus as S, CaregiverAvailability, PropertyManager
from pawpal.schemas import ExtractedBooking, ExtractedTimeWindow
from pawpal.scheduling import Window, generate_visits, recommend_schedule, set_time_windows
from tests import factories as f
from tests.conftest import FakeOpenAI


class FakeChat:
    def __init__(self, text=None, raises=None):
        self.calls = []
        outer = self

        class _Completions:
            def create(self, **kw):
                outer.calls.append(kw)
                if raises:
                    raise raises
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

        self.chat = SimpleNamespace(completions=_Completions())


def _rec(session):
    f.caregiver(session, name="Maya Carer")
    r = f.resident(session)
    p = f.pet(session, r)
    p.medical_notes = "SECRET-MEDICAL"
    b = f.booking(session, r, p)
    b.special_instructions = "SECRET-INSTRUCTION"
    set_time_windows(session, b, [Window(time(7), time(10)), Window(time(17), time(20))])
    generate_visits(session, b)
    return recommend_schedule(session, b)


def test_ai_explains_but_receives_no_sensitive_data(session):
    rec = _rec(session)
    client = FakeChat(text="Maya will visit Luna twice a day.")
    text, source = explain_recommendation(rec, client=client)
    assert (text, source) == ("Maya will visit Luna twice a day.", "ai")
    sent = str(client.calls[0]["messages"])
    assert "SECRET" not in sent and "$" not in sent and "price" not in sent.split("Rules")[0]
    assert "Never mention prices" in client.calls[0]["messages"][0]["content"]


def test_ai_explanation_falls_back_to_template(session):
    rec = _rec(session)
    text, source = explain_recommendation(rec)  # no API key in tests
    assert source == "template" and "Maya" in text and "8 of 8" in text
    text, source = explain_recommendation(rec, client=FakeChat(raises=RuntimeError("down")))
    assert source == "template"


def test_template_mentions_alternatives():
    facts = {"building": "B", "total_visits": 2, "assigned_visits": 1, "visits_per_caregiver": {"Ann": 1},
             "top_candidates": [], "unassigned": [{"date": "2026-11-06", "window": "21:00–22:00", "why": []}],
             "alternatives": [{"date": "2026-11-06", "time": "20:30–21:00", "caregiver": "Ann"}], "excluded": {}}
    assert "need your approval" in template_explanation(facts)


def test_extraction_returns_stated_time_windows():
    parsed = ExtractedBooking(pet_name="Luna", visits_per_day=2, visit_duration_minutes=60,
                              time_windows=[ExtractedTimeWindow(start="08:00"), ExtractedTimeWindow(start="18:00", end="19:30")])
    out = extract_booking("Luna, Nov 6-9 2026, an hour at 8am and 6-7:30pm", ["Luna"], today=date(2026, 10, 20),
                          client=FakeOpenAI(parsed=parsed))
    assert out.visit_duration_minutes == 60 and [w.start for w in out.time_windows] == ["08:00", "18:00"]
    with pytest.raises(ValueError):
        ExtractedTimeWindow(start="8am")


def _seed_weeks_1_and_2(engine):
    from pawpal.seed_data import seed_database
    from pawpal.seed_scheduling import seed_scheduling_data

    return {"week1": seed_database(engine), "week2": seed_scheduling_data(engine)}


def test_week2_seed_is_reproducible_and_idempotent(file_engine):
    first = _seed_weeks_1_and_2(file_engine)
    second = _seed_weeks_1_and_2(file_engine)
    assert first["week2"]["bookings"] == 13 and first["week2"]["managers"] == 3
    assert all(v == 0 for v in second["week1"].values()) and all(v == 0 for v in second["week2"].values())
    with session_scope(file_engine) as s:
        statuses = dict(s.execute(select(Booking.status, func.count()).group_by(Booking.status)).all())
        assert statuses[S.CONFIRMED] == 5
        assert statuses[S.PENDING_ADMIN_APPROVAL] == 3
        assert statuses[S.PENDING_RESIDENT_APPROVAL] == 3
        assert statuses[S.NEEDS_RESCHEDULING] == 1
        assert statuses[S.DRAFT] == 1
        assert s.scalar(select(func.count()).select_from(PropertyManager)) == 3
        assert s.scalar(select(func.count()).select_from(CaregiverAvailability)) > 400
