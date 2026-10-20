from datetime import date, time

import pytest

from pawpal.actors import Actor
from pawpal.availability import request_time_off
from pawpal.models import ServiceType, Visit, VisitStatus
from pawpal.schemas import ExtractedTimeWindow
from pawpal.scheduling import (
    MissingTimeWindowsError,
    SchedulingError,
    Window,
    apply_recommendation,
    check_booking_schedule,
    expected_visit_count,
    generate_visits,
    recommend_schedule,
    set_time_windows,
    validate_windows,
    windows_from_extracted,
)
from tests import factories as f

AM, PM = Window(time(7), time(10)), Window(time(17), time(20))


def _booking(session, building=f.B1, species="cat", **kw):
    r = f.resident(session, building=building)
    p = f.pet(session, r, species=species)
    return f.booking(session, r, p, **kw)


def _confirmed(session, cg, day, start, end, building=f.B1):
    """Another resident's confirmed visit held by ``cg``."""
    b = _booking(session, building=building, start=day, end=day, visits_per_day=1)
    session.add(Visit(booking_id=b.id, caregiver_id=cg.id, visit_date=day, slot=1, window_start=start,
                      window_end=end, start_time=start, end_time=end, duration_minutes=30,
                      service_type=ServiceType.FEEDING, assignment_status=VisitStatus.CONFIRMED))
    session.flush()


# ------------------------------------------------------------ visit generation
def test_nov_6_to_9_twice_daily_generates_eight_visits(session):
    b = _booking(session)
    set_time_windows(session, b, [AM, PM])
    visits = generate_visits(session, b)
    assert expected_visit_count(b) == len(visits) == 8
    assert {v.visit_date for v in visits} == {date(2026, 11, d) for d in (6, 7, 8, 9)}
    v = visits[0]
    assert (v.booking_id, v.slot, v.start_time, v.end_time, v.duration_minutes) == (b.id, 1, time(7), time(7, 30), 30)
    assert v.service_type == ServiceType.FEEDING and v.assignment_status == VisitStatus.UNASSIGNED
    assert v.caregiver_id is None


def test_missing_time_windows_asks_instead_of_inventing(session):
    b = _booking(session)
    with pytest.raises(MissingTimeWindowsError) as exc:
        generate_visits(session, b)
    assert len(exc.value.questions) == 2
    assert b.visits == []
    set_time_windows(session, b, [AM, PM])  # still fine afterwards


def test_window_validation():
    assert validate_windows([AM, PM], 2, 30) == []
    assert validate_windows([AM], 2, 30)                                       # wrong count
    assert validate_windows([Window(time(7), time(7, 20))], 1, 30)             # too short
    assert validate_windows([Window(time(7), time(10)), Window(time(9), time(11))], 2, 30)  # overlap
    assert validate_windows([PM, AM], 2, 30)                                   # out of order
    assert validate_windows([Window(time(7), time(7, 45))], 1, 60)             # too short for 60 min


def test_extracted_times_become_windows_only_when_complete():
    items = [ExtractedTimeWindow(start="08:00"), ExtractedTimeWindow(start="17:30", end="19:00")]
    windows, questions = windows_from_extracted(items, 2)
    assert windows == [Window(time(8), time(9)), Window(time(17, 30), time(19))] and questions == []
    windows, questions = windows_from_extracted(items[:1], 2)
    assert windows == [] and questions
    windows, questions = windows_from_extracted([], 2)
    assert windows == [] and questions


def test_regeneration_after_duration_change(session):
    b = _booking(session)
    set_time_windows(session, b, [AM, PM])
    generate_visits(session, b)
    b.visit_duration_minutes = 60
    visits = generate_visits(session, b)
    assert len(visits) == 8 and all(v.end_time.hour - v.start_time.hour == 1 for v in visits)


# ------------------------------------------------------------------- matching
def _prepared(session, **kw):
    b = _booking(session, **kw)
    set_time_windows(session, b, [AM, PM] if kw.get("visits_per_day", 2) == 2 else [AM])
    generate_visits(session, b)
    return b


def test_service_and_pet_type_compatibility(session):
    f.caregiver(session, name="Dog Only", species=("dog",))
    f.caregiver(session, name="No Feeding", services=(ServiceType.WALKING,))
    f.caregiver(session, name="Other Building", buildings=(f.B2,))
    f.caregiver(session, name="Inactive", active=False)
    good = f.caregiver(session, name="Cat Feeder", species=("cat",), services=(ServiceType.FEEDING,), buildings=(f.B1,))
    b = _prepared(session)
    rec = recommend_schedule(session, b)
    assert rec.fully_assigned and {p.caregiver_id for p in rec.proposals} == {good.id}
    reasons = {c.name: c.reasons for c in rec.candidates}
    assert "does not care for cats" in reasons["Dog Only"]
    assert any("feeding" in r for r in reasons["No Feeding"])
    assert "does not cover The Meridian" in reasons["Other Building"]
    assert "inactive" in reasons["Inactive"]


def test_time_off_is_respected(session):
    a = f.caregiver(session, name="Ann")
    b_cg = f.caregiver(session, name="Ben")
    request_time_off(session, a.id, Actor.caregiver(a.id), date(2026, 11, 7), date(2026, 11, 7))
    b = _prepared(session)
    rec = recommend_schedule(session, b)
    assert rec.fully_assigned
    assert all(p.caregiver_id == b_cg.id for p in rec.proposals if p.day == date(2026, 11, 7))
    # Ben can cover all 8 visits, Ann only 6 -> continuity puts everything on Ben.
    assert {p.caregiver_id for p in rec.proposals} == {b_cg.id}


def test_overlapping_visits_and_travel_buffer(session):
    cg = f.caregiver(session, name="Solo")
    # 07:00-07:30 confirmed in another building: needs 30-min buffer -> earliest 08:00.
    _confirmed(session, cg, date(2026, 11, 6), time(7), time(7, 30), building=f.B2)
    # 17:00-19:40 confirmed in the same building: buffer 10 min -> 19:50 would end 20:20 > window.
    _confirmed(session, cg, date(2026, 11, 6), time(17), time(19, 40))
    b = _prepared(session)
    rec = recommend_schedule(session, b)
    nov6 = [p for p in rec.proposals if p.day == date(2026, 11, 6)]
    assert nov6[0].caregiver_id == cg.id and nov6[0].start == time(8)
    assert nov6[1].caregiver_id is None and "already confirmed" in nov6[1].blocked_by["Solo"]


def test_prefers_caregiver_already_working_in_building(session):
    elsewhere = f.caregiver(session, name="Elsewhere")
    local = f.caregiver(session, name="Local")
    _confirmed(session, local, date(2026, 11, 6), time(12), time(12, 30))  # same building, no clash
    b = _prepared(session)
    rec = recommend_schedule(session, b)
    assert {p.caregiver_id for p in rec.proposals} == {local.id}
    assert rec.candidates[0].caregiver_id == local.id and elsewhere.id != local.id


def test_no_caregiver_suggests_alternatives(session):
    f.caregiver(session, name="Early Bird", blocks=(f.Block(time(6), time(9, 30)),))
    b = _booking(session, visits_per_day=1, start=date(2026, 11, 6), end=date(2026, 11, 6))
    set_time_windows(session, b, [Window(time(10), time(12))])
    generate_visits(session, b)
    rec = recommend_schedule(session, b)
    assert not rec.fully_assigned
    alt = rec.alternatives[rec.proposals[0].visit_id][0]
    assert (alt.caregiver_name, alt.start, alt.end, alt.minutes_outside_window) == ("Early Bird", time(9), time(9, 30), 60)  # starts 60 min before the window


def test_check_booking_schedule_flags_problems(session):
    cg = f.caregiver(session)
    b = _prepared(session)
    assert {c.kind for c in check_booking_schedule(session, b)} == {"unassigned"}
    rec = recommend_schedule(session, b)
    apply_recommendation(session, b, rec)
    assert check_booking_schedule(session, b) == []
    request_time_off(session, cg.id, Actor.caregiver(cg.id), date(2026, 11, 8), date(2026, 11, 8))
    kinds = [c.kind for c in check_booking_schedule(session, b)]
    assert kinds == ["time_off", "time_off"]


def test_confirmed_booking_cannot_be_regenerated(session):
    f.caregiver(session)
    b = _prepared(session)
    for v in b.visits:
        v.assignment_status = VisitStatus.CONFIRMED
    with pytest.raises(SchedulingError):
        generate_visits(session, b)
