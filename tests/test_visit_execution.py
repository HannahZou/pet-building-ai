from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from pawpal.actors import Actor, AuthorizationError
from pawpal.audit import events_for
from pawpal.database import session_scope
from pawpal.models import (
    Booking, ExecutionStatus as E, Notification, NotificationType, VisitExecution,
)
from pawpal.timeutil import as_utc
from pawpal.visit_execution import (
    ExecutionError,
    InvalidVisitTransition,
    _transition,
    check_in,
    check_out,
    get_execution,
    resolve_review,
    run_daily_checks,
)
from pawpal.care_logs import log_activity
from pawpal.models import ActivityType as A, CompletionStatus as C
from pawpal.workflow import cancel_booking
from tests.week3_world import DAY1, DAY2, at, confirmed_world


def _complete_feeding(s, visit_id, actor):
    log_activity(s, visit_id, actor, A.FEEDING, C.COMPLETED, "Ate everything")
    log_activity(s, visit_id, actor, A.FRESH_WATER, C.COMPLETED)


def test_confirmation_creates_scheduled_executions(file_engine):
    w = confirmed_world(file_engine)
    with session_scope(file_engine) as s:
        statuses = [get_execution(s, v).execution_status for v in w.visits[DAY1] + w.visits[DAY2]]
    assert statuses == [E.SCHEDULED] * 4


def test_valid_check_in_and_check_out(file_engine):
    w = confirmed_world(file_engine)
    v = w.visits[DAY1][0]
    with session_scope(file_engine) as s:
        ex = check_in(s, v, w.caregiver, now=at(DAY1, 7, 4))
        assert ex.execution_status == E.IN_PROGRESS and as_utc(ex.actual_check_in) == at(DAY1, 7, 4)
        _complete_feeding(s, v, w.caregiver)
    with session_scope(file_engine) as s:
        res = check_out(s, v, w.caregiver, now=at(DAY1, 7, 33))
        assert res.execution.execution_status == E.COMPLETED and res.execution.duration_minutes == 29
        assert not res.needs_review and not res.day_complete  # evening visit still to come
        actions = [(e.action, e.details.get("to")) for e in events_for(s, "visit", v)]
        assert ("status_changed", "in_progress") in actions and ("status_changed", "completed") in actions
        types = {n.notification_type for n in s.query(Notification).filter(Notification.booking_id == w.booking)}
        assert {NotificationType.VISIT_STARTED, NotificationType.VISIT_COMPLETED} <= types


def test_unauthorized_check_in(file_engine):
    w = confirmed_world(file_engine)
    v = w.visits[DAY1][0]
    with session_scope(file_engine) as s:
        from tests import factories as f
        other = f.caregiver(s, name="Not Assigned", days=None).id
    for actor in (Actor.caregiver(other), w.res_actor, w.admin_actor):
        with pytest.raises(AuthorizationError):
            with session_scope(file_engine) as s:
                check_in(s, v, actor, now=at(DAY1, 7))
    with pytest.raises(AuthorizationError):
        with session_scope(file_engine) as s:
            check_out(s, v, Actor.caregiver(other), now=at(DAY1, 8))


def test_duplicate_check_in_and_check_out_prevented(file_engine):
    w = confirmed_world(file_engine)
    v = w.visits[DAY1][0]
    with session_scope(file_engine) as s:
        check_in(s, v, w.caregiver, now=at(DAY1, 7))
    with pytest.raises(InvalidVisitTransition):
        with session_scope(file_engine) as s:
            check_in(s, v, w.caregiver, now=at(DAY1, 7, 5))
    with session_scope(file_engine) as s:
        _complete_feeding(s, v, w.caregiver)
        check_out(s, v, w.caregiver, now=at(DAY1, 7, 30))
    with pytest.raises(InvalidVisitTransition):
        with session_scope(file_engine) as s:
            check_out(s, v, w.caregiver, now=at(DAY1, 7, 40))
    with session_scope(file_engine) as s:
        assert s.query(VisitExecution).filter(VisitExecution.scheduled_visit_id == v).count() == 1
        assert as_utc(get_execution(s, v).actual_check_out) == at(DAY1, 7, 30)


def test_check_out_rules(file_engine):
    w = confirmed_world(file_engine)
    v = w.visits[DAY1][0]
    with pytest.raises(InvalidVisitTransition, match="before checking in"):
        with session_scope(file_engine) as s:
            check_out(s, v, w.caregiver, now=at(DAY1, 7, 30))
    with session_scope(file_engine) as s:
        check_in(s, v, w.caregiver, now=at(DAY1, 7, 10))
    with pytest.raises(ExecutionError, match="after check-in"):
        with session_scope(file_engine) as s:
            check_out(s, v, w.caregiver, now=at(DAY1, 7, 10))
    with pytest.raises(IntegrityError):  # the database enforces it too
        with session_scope(file_engine) as s:
            ex = get_execution(s, v)
            ex.actual_check_out = ex.actual_check_in - timedelta(minutes=5)


def test_only_confirmed_visits_on_their_day_can_start(file_engine):
    w = confirmed_world(file_engine, confirm=False)  # pending admin approval
    with pytest.raises(ExecutionError, match="confirmed"):
        with session_scope(file_engine) as s:
            check_in(s, w.visits[DAY1][0], w.caregiver, now=at(DAY1, 7))
    from tests import factories as f
    w2 = confirmed_world(file_engine, building=f.B2)
    with pytest.raises(ExecutionError, match="only possible on that day"):
        with session_scope(file_engine) as s:
            check_in(s, w2.visits[DAY2][0], w2.caregiver, now=at(DAY1, 7))


def test_invalid_status_transitions(file_engine):
    w = confirmed_world(file_engine)
    v = w.visits[DAY1][0]
    with session_scope(file_engine) as s:
        ex = get_execution(s, v)
        with pytest.raises(InvalidVisitTransition):
            _transition(s, ex, E.COMPLETED, w.caregiver)  # Scheduled -> Completed is not allowed
    with session_scope(file_engine) as s:
        run_daily_checks(s, now=at(DAY2, 9))  # Nov 6 has ended without check-ins
        assert get_execution(s, v).execution_status == E.MISSED
    with pytest.raises(InvalidVisitTransition):
        with session_scope(file_engine) as s:
            check_in(s, v, w.caregiver, now=at(DAY1, 23, 30))  # right day, but the visit is already Missed


def test_daily_checks_never_mark_completed(file_engine):
    w = confirmed_world(file_engine)
    morning, evening = w.visits[DAY1]
    with session_scope(file_engine) as s:
        check_in(s, morning, w.caregiver, now=at(DAY1, 7))  # never checked out
    with session_scope(file_engine) as s:
        assert run_daily_checks(s, now=at(DAY1, 23)).missed == 0  # same local day: nothing yet
    with session_scope(file_engine) as s:
        result = run_daily_checks(s, now=at(DAY2, 6))
        assert (result.missed, result.unclosed) == (1, 1)
        assert get_execution(s, morning).execution_status == E.NEEDS_REVIEW
        assert get_execution(s, morning).actual_check_out is None
        assert get_execution(s, evening).execution_status == E.MISSED
        assert result.completed_days == [(w.booking, DAY1)]
        assert s.query(Notification).filter(Notification.notification_type == NotificationType.MISSED_VISIT).count() == 2  # in-app + email


def test_resolve_review_requires_building_manager(file_engine):
    w = confirmed_world(file_engine)
    v = w.visits[DAY1][0]
    with session_scope(file_engine) as s:
        run_daily_checks(s, now=at(DAY2, 6))
    with pytest.raises(AuthorizationError):
        with session_scope(file_engine) as s:
            resolve_review(s, v, Actor.manager(w.remote), "Called the caregiver")
    with session_scope(file_engine) as s:
        ex = resolve_review(s, v, w.admin_actor, "Called the caregiver; resident informed")
        assert ex.execution_status == E.MISSED and ex.reviewed_by == w.admin


def test_cancelling_booking_cancels_unstarted_visits_only(file_engine):
    w = confirmed_world(file_engine)
    started = w.visits[DAY1][0]
    with session_scope(file_engine) as s:
        check_in(s, started, w.caregiver, now=at(DAY1, 7))
    with session_scope(file_engine) as s:
        cancel_booking(s, w.booking, w.res_actor, "Came home early")
    with session_scope(file_engine) as s:
        assert get_execution(s, started).execution_status == E.IN_PROGRESS
        assert [get_execution(s, v).execution_status for v in w.visits[DAY2]] == [E.CANCELLED, E.CANCELLED]
        assert s.get(Booking, w.booking).visits[0].assignment_status.value == "confirmed"
