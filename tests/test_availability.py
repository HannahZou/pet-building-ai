from datetime import date, time

import pytest

from pawpal.actors import Actor, AuthorizationError
from pawpal.availability import (
    AvailabilityError,
    Block,
    apply_weekly_template,
    blocks_on,
    caregiver_visits,
    list_time_off,
    mark_day_off,
    request_time_off,
    set_capabilities,
    set_day_availability,
)
from pawpal.models import ServiceType, Visit, VisitStatus
from tests import factories as f

DAY = date(2026, 11, 10)


def test_set_and_update_working_hours(session):
    cg = f.caregiver(session, days=None)
    me = Actor.caregiver(cg.id)
    set_day_availability(session, cg.id, DAY, [Block(time(8), time(12))], me)
    assert blocks_on(session, cg.id, DAY) == [Block(time(8), time(12))]
    set_day_availability(session, cg.id, DAY, [Block(time(14), time(18)), Block(time(7), time(9))], me)
    assert [b.label() for b in blocks_on(session, cg.id, DAY)] == ["07:00–09:00", "14:00–18:00"]
    set_day_availability(session, cg.id, DAY, [], me)  # not working
    assert blocks_on(session, cg.id, DAY) == []


def test_invalid_blocks_rejected(session):
    cg = f.caregiver(session, days=None)
    me = Actor.caregiver(cg.id)
    with pytest.raises(AvailabilityError):
        set_day_availability(session, cg.id, DAY, [Block(time(12), time(8))], me)
    with pytest.raises(AvailabilityError):
        set_day_availability(session, cg.id, DAY, [Block(time(8), time(12)), Block(time(11), time(13))], me)


def test_weekly_template_only_sets_chosen_weekdays(session):
    cg = f.caregiver(session, days=None)
    n = apply_weekly_template(session, cg.id, Actor.caregiver(cg.id), date(2026, 11, 2), date(2026, 11, 15),
                              weekdays=[0, 2, 4], blocks=[Block(time(9), time(17))])
    assert n == 6  # Mon/Wed/Fri over two weeks
    assert blocks_on(session, cg.id, date(2026, 11, 3)) == []          # Tuesday untouched
    assert blocks_on(session, cg.id, date(2026, 11, 4)) == [Block(time(9), time(17))]


def test_caregiver_cannot_edit_someone_else(session):
    a = f.caregiver(session, name="A", days=None)
    b = f.caregiver(session, name="B", days=None)
    with pytest.raises(AuthorizationError):
        set_day_availability(session, b.id, DAY, [Block(time(8), time(9))], Actor.caregiver(a.id))
    with pytest.raises(AuthorizationError):
        request_time_off(session, b.id, Actor.caregiver(a.id), DAY, DAY)
    with pytest.raises(AuthorizationError):
        set_capabilities(session, b.id, Actor.resident(1), ["cat"], [], [])


def test_capabilities(session):
    cg = f.caregiver(session, days=None)
    set_capabilities(session, cg.id, Actor.caregiver(cg.id), ["Dog", "cat"], [ServiceType.WALKING], [f.B2])
    assert cg.species_set == {"dog", "cat"}
    assert cg.service_set == {ServiceType.WALKING}
    assert cg.building_set == {f.B2}


def test_time_off_and_day_off(session):
    cg = f.caregiver(session)
    me = Actor.caregiver(cg.id)
    request_time_off(session, cg.id, me, date(2026, 11, 20), date(2026, 11, 22), "Trip")
    mark_day_off(session, cg.id, me, date(2026, 11, 25))
    assert [(t.start_date, t.end_date) for t in list_time_off(session, cg.id)] == [
        (date(2026, 11, 20), date(2026, 11, 22)), (date(2026, 11, 25), date(2026, 11, 25))]
    with pytest.raises(AvailabilityError):
        request_time_off(session, cg.id, me, date(2026, 11, 22), date(2026, 11, 20))


def _confirmed_visit(session, cg):
    r = f.resident(session)
    p = f.pet(session, r)
    b = f.booking(session, r, p, start=DAY, end=DAY, visits_per_day=1)
    v = Visit(booking_id=b.id, caregiver_id=cg.id, visit_date=DAY, slot=1, window_start=time(8), window_end=time(10),
              start_time=time(8), end_time=time(8, 30), duration_minutes=30, service_type=ServiceType.FEEDING,
              assignment_status=VisitStatus.CONFIRMED)
    session.add(v)
    session.flush()
    return v


def test_cannot_take_time_off_or_drop_hours_over_confirmed_visit(session):
    cg = f.caregiver(session)
    _confirmed_visit(session, cg)
    me = Actor.caregiver(cg.id)
    with pytest.raises(AvailabilityError, match="confirmed visit"):
        request_time_off(session, cg.id, me, DAY, DAY)
    with pytest.raises(AvailabilityError, match="confirmed visit"):
        set_day_availability(session, cg.id, DAY, [Block(time(12), time(18))], me)
    assert [v.visit_date for v in caregiver_visits(session, cg.id, DAY, DAY)] == [DAY]
