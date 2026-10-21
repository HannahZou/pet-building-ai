from sqlalchemy import func, select

from pawpal.database import session_scope
from pawpal.models import (
    CareActivityLog, CompletionStatus, DailyCareReport, DeliveryStatus, ExecutionStatus as E, Notification,
    NotificationType, PetCarePhoto, ReportStatus, VisitExecution,
)
from pawpal.seed_data import seed_all


def test_week3_seed_covers_scenarios_and_is_idempotent(file_engine):
    first = seed_all(file_engine)
    second = seed_all(file_engine)
    assert first["week3"] == {"bookings": 4, "visits_played": 10, "reports": 6, "approved": 2, "live_demo": 1}
    assert all(v == 0 for week in second.values() for v in week.values())
    with session_scope(file_engine) as s:
        statuses = dict(s.execute(select(VisitExecution.execution_status, func.count()).group_by(VisitExecution.execution_status)).all())
        assert statuses[E.COMPLETED] >= 7 and statuses[E.MISSED] == 1
        assert statuses[E.IN_PROGRESS] == 1 and statuses[E.NEEDS_REVIEW] == 1
        assert statuses[E.SCHEDULED] >= 4                       # Week 2 confirmed visits + live demo
        assert s.scalar(select(func.count()).select_from(PetCarePhoto)) == 5
        logs = s.scalars(select(CareActivityLog)).all()
        assert any(l.activity_type.value == "dog_walking" for l in logs)
        assert any(l.completion_status == CompletionStatus.NOT_COMPLETED for l in logs)
        assert any(l.flagged for l in logs)
        reports = s.scalars(select(DailyCareReport)).all()
        assert sorted(r.review_status.value for r in reports).count("approved") == 2
        assert sum(r.needs_attention for r in reports) == 3  # Oscar missed, Finn issues, Coco no check-out
        assert all(r.evaluation["final"]["unsupported_claims"] == [] for r in reports)
        pending = s.scalar(select(func.count()).select_from(Notification).where(Notification.delivery_status == DeliveryStatus.PENDING))
        assert pending > 0
        assert s.scalar(select(func.count()).select_from(Notification).where(
            Notification.notification_type == NotificationType.MISSED_VISIT)) == 2
