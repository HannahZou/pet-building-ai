"""KPI calculations: hand-computed expectations on small controlled datasets."""
from datetime import date, datetime, time, timedelta, timezone

import pytest

from pawpal.access import analytics_scope
from pawpal.actors import Actor, AuthorizationError
from pawpal.analytics.data import AnalyticsFilter
from pawpal.analytics.kpis import KPI_DEFINITIONS, compute_snapshot, definitions_markdown, ratio
from pawpal.availability import request_time_off
from pawpal.care_logs import log_activity
from pawpal.database import session_scope
from pawpal.models import (
    ActivityType as A, Booking, BookingEvent, CompletionStatus as C, PlatformAdmin, Resident, ServiceType,
)
from pawpal.visit_execution import check_in, check_out, run_daily_checks
from tests import factories as f
from tests.week3_world import DAY1, DAY2, at, confirmed_world

AS_OF = datetime(2026, 12, 1, tzinfo=timezone.utc)
NOV = AnalyticsFilter(date(2026, 11, 1), date(2026, 11, 30), data_source="real")


def _snap(engine, actor, flt=NOV):
    with session_scope(engine) as s:
        return compute_snapshot(s, actor, flt, as_of=AS_OF)


def test_ratio_handles_zero_denominator():
    m = ratio("visit_completion_rate", 0, 0)
    assert m.value is None and m.status == "no_data" and m.display() == "No data"
    assert ratio("visit_completion_rate", 3, 4).value == 0.75


def test_empty_dataset_gives_no_data_not_zero(file_engine):
    with session_scope(file_engine) as s:
        mid = f.manager(s).id
    snap = _snap(file_engine, Actor.manager(mid))
    assert snap.metrics["total_bookings"].value == 0
    for key in ("booking_conversion_rate", "visit_completion_rate", "scheduling_conflict_rate", "repeat_booking_rate",
                "notification_success_rate", "average_booking_value", "avg_booking_processing_hours"):
        assert snap.metrics[key].value is None and snap.metrics[key].status == "no_data", key
    assert snap.metrics["customer_satisfaction"].status == "not_available"
    assert snap.metrics["resident_retention_rate"].status == "insufficient_data"
    assert snap.weekly == [] and snap.revenue_by_building == {}


def test_booking_counted_once_and_revenue(file_engine):
    w = confirmed_world(file_engine)           # 4 visits, quote 4 x $30 = $120
    with session_scope(file_engine) as s:
        r = s.get(Booking, w.booking).resident
        p = s.get(Booking, w.booking).pet
        f.booking(s, r, p, start=date(2026, 11, 20), end=date(2026, 11, 21))   # a draft: not a submitted booking
        s.get(Booking, w.booking).created_at = datetime(2026, 10, 20, 9, 0)   # tests must not depend on today's date
    snap = _snap(file_engine, w.admin_actor)
    m = snap.metrics
    assert (m["total_bookings"].value, m["confirmed_bookings"].value) == (1, 1)
    assert m["gross_booking_value"].value == 120.0 and m["average_booking_value"].value == 120.0
    assert snap.revenue_by_building == {f.B1: 120.0} and snap.revenue_by_service == {"feeding": 120.0}
    assert m["total_scheduled_visits"].value == 4
    assert m["booking_conversion_rate"].status == "no_data"   # requests were made in October, not November
    wide = AnalyticsFilter(date(2026, 10, 1), date(2026, 11, 30), data_source="real")
    assert _snap(file_engine, w.admin_actor, wide).metrics["booking_conversion_rate"].value == 1.0  # draft excluded


def test_visit_completion_missed_and_due(file_engine):
    w = confirmed_world(file_engine)
    m1, m2 = w.visits[DAY1]
    with session_scope(file_engine) as s:
        check_in(s, m1, w.caregiver, now=at(DAY1, 7))
        log_activity(s, m1, w.caregiver, A.FEEDING, C.COMPLETED)
        log_activity(s, m1, w.caregiver, A.FRESH_WATER, C.COMPLETED)
        check_out(s, m1, w.caregiver, now=at(DAY1, 7, 30))
    with session_scope(file_engine) as s:
        run_daily_checks(s, now=at(DAY2, 8))      # m2 missed
    with session_scope(file_engine) as s:
        snap = compute_snapshot(s, w.admin_actor, NOV, as_of=at(DAY2, 9))  # only Nov 6 visits are due
    m = snap.metrics
    assert (m["completed_visits"].value, m["missed_visits"].value) == (1, 1)
    assert (m["visit_completion_rate"].numerator, m["visit_completion_rate"].denominator) == (1, 2)


def test_caregiver_utilization_with_time_off(file_engine):
    w = confirmed_world(file_engine)
    flt = AnalyticsFilter(DAY1, DAY2, data_source="real")
    snap = _snap(file_engine, w.admin_actor, flt)
    u = snap.metrics["caregiver_utilization_rate"]
    # Maya: 2 days x (5h + 5h) = 1200 available minutes; 4 visits x 30 = 120 assigned
    assert (u.numerator, u.denominator, u.value) == (120, 1200, 0.1)
    with session_scope(file_engine) as s:
        other = f.caregiver(s, name="Second Carer", buildings=(f.B1,)).id
        request_time_off(s, other, Actor.caregiver(other), DAY2, DAY2)
    u = _snap(file_engine, w.admin_actor, flt).metrics["caregiver_utilization_rate"]
    assert u.denominator == 1200 + 600  # second carer: 1 working day after time off


def test_processing_and_assignment_times(file_engine):
    w = confirmed_world(file_engine)
    created = datetime(2026, 10, 20, 9, 0)
    with session_scope(file_engine) as s:
        b = s.get(Booking, w.booking)
        b.created_at = created
        for ev in s.query(BookingEvent).filter(BookingEvent.booking_id == w.booking):
            if ev.to_status == "pending_resident_approval":
                ev.created_at = created + timedelta(hours=1)
            if ev.to_status == "confirmed":
                ev.created_at = created + timedelta(hours=30)
    flt = AnalyticsFilter(date(2026, 10, 1), date(2026, 11, 30), data_source="real")
    m = _snap(file_engine, w.admin_actor, flt).metrics
    assert m["avg_booking_processing_hours"].value == pytest.approx(30)
    assert m["avg_assignment_hours"].value == pytest.approx(1)
    assert m["scheduling_conflict_rate"].value == 0.0 and m["scheduling_conflict_rate"].denominator == 2


def test_date_range_and_service_filters(file_engine):
    w = confirmed_world(file_engine)
    dec = AnalyticsFilter(date(2026, 12, 1), date(2026, 12, 31), data_source="real")
    assert _snap(file_engine, w.admin_actor, dec).metrics["total_bookings"].value == 0
    walking = AnalyticsFilter(NOV.start, NOV.end, service_types=(ServiceType.WALKING,), data_source="real")
    assert _snap(file_engine, w.admin_actor, walking).metrics["total_bookings"].value == 0
    assert _snap(file_engine, w.admin_actor).metrics["total_bookings"].value == 1


def test_synthetic_and_real_records_never_mix(file_engine):
    w = confirmed_world(file_engine)  # resident is not synthetic
    synthetic = AnalyticsFilter(NOV.start, NOV.end, data_source="synthetic")
    assert _snap(file_engine, w.admin_actor, synthetic).metrics["total_bookings"].value == 0
    with session_scope(file_engine) as s:
        s.get(Resident, w.resident).is_synthetic = True
    assert _snap(file_engine, w.admin_actor, synthetic).metrics["total_bookings"].value == 1
    assert _snap(file_engine, w.admin_actor).metrics["total_bookings"].value == 0


def test_cross_building_access_is_enforced(file_engine):
    w = confirmed_world(file_engine)  # building B1
    with session_scope(file_engine) as s:
        b2_manager = f.manager(s, building=f.B2, name="B2 Manager").id
        b1_manager = f.manager(s, building=f.B1, name="B1 Manager").id
        no_perm = PlatformAdmin(full_name="No Perm", email="np@example.test", permissions=[])
        reader = PlatformAdmin(full_name="Reader", email="rd@example.test", permissions=["analytics:read"])
        s.add_all([no_perm, reader])
        s.flush()
        no_perm_id, reader_id = no_perm.id, reader.id
    assert _snap(file_engine, Actor.manager(b2_manager)).metrics["total_bookings"].value == 0
    assert _snap(file_engine, Actor.manager(b1_manager)).metrics["total_bookings"].value == 1
    with pytest.raises(AuthorizationError):
        _snap(file_engine, Actor.manager(b2_manager), AnalyticsFilter(NOV.start, NOV.end, buildings=(f.B1,), data_source="real"))
    for actor in (w.res_actor, w.caregiver, Actor.admin(no_perm_id), Actor("system", None), Actor.manager(999)):
        with pytest.raises(AuthorizationError):
            _snap(file_engine, actor)
    assert _snap(file_engine, Actor.admin(reader_id)).metrics["total_bookings"].value == 1
    with session_scope(file_engine) as s:
        scope = analytics_scope(s, Actor.admin(reader_id))
        assert not scope.can_export and not scope.can_read_evaluation and not scope.can_run_evaluation


def test_kpi_definitions_are_complete():
    for key, d in KPI_DEFINITIONS.items():
        assert d.formula and d.window and d.missing, key
    md = definitions_markdown()
    assert md.count("\n") == len(KPI_DEFINITIONS) + 1
