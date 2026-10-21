"""Pet-care activity logging.

* A caregiver records what *actually* happened at a visit they are performing.
* Nothing is assumed: an activity is completed only if a log says so. At check-out
  every essential activity without a log is stored explicitly as NOT_RECORDED.
* Essential activities depend on the booked service and the species.
* Not completed / skipped / not recorded essentials and flagged observations send
  the visit to human review.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .actors import Actor, AuthorizationError
from .audit import record
from .models import (
    ActivityType as A,
    CareActivityLog,
    CompletionStatus as C,
    ExecutionStatus,
    ServiceType,
    Visit,
    VisitExecution,
)
from .timeutil import utc_now

ACTIVITY_LABELS = {
    A.FEEDING: "Feeding",
    A.FRESH_WATER: "Fresh water",
    A.DOG_WALKING: "Dog walking",
    A.LITTER_BOX: "Litter box cleaning",
    A.PLAYTIME: "Playtime",
    A.WELLNESS_OBSERVATION: "Wellness observation",
    A.OTHER: "Other",
}
STATUS_LABELS = {
    C.COMPLETED: "Completed",
    C.NOT_COMPLETED: "Not completed",
    C.SKIPPED: "Skipped",
    C.NOT_RECORDED: "Not recorded",
}
MAX_NOTE = 1000


class CareLogError(Exception):
    pass


def essential_activities(service: ServiceType, species: str) -> list[A]:
    species = (species or "").lower()
    if service == ServiceType.FEEDING:
        return [A.FEEDING, A.FRESH_WATER]
    if service == ServiceType.WALKING:
        return [A.DOG_WALKING, A.FRESH_WATER]
    if service == ServiceType.CHECK_IN:
        return [A.WELLNESS_OBSERVATION]
    out = [A.FEEDING, A.FRESH_WATER]  # full care
    if species == "dog":
        out.append(A.DOG_WALKING)
    if species == "cat":
        out.append(A.LITTER_BOX)
    return out


def checklist_for(visit: Visit) -> list[A]:
    """Essentials first, then optional activities that make sense for the species."""
    species = visit.booking.pet.species.lower()
    essentials = essential_activities(visit.service_type, species)
    optional = [A.FEEDING, A.FRESH_WATER, A.PLAYTIME]
    optional += [A.DOG_WALKING] if species == "dog" else []
    optional += [A.LITTER_BOX] if species == "cat" else []
    optional += [A.WELLNESS_OBSERVATION]
    return essentials + [a for a in dict.fromkeys(optional) if a not in essentials]


def logs_for_visit(session: Session, visit_id: int) -> list[CareActivityLog]:
    return list(session.scalars(
        select(CareActivityLog).where(CareActivityLog.scheduled_visit_id == visit_id).order_by(CareActivityLog.id)
    ))


def _execution(session: Session, visit_id: int) -> VisitExecution | None:
    return session.scalar(select(VisitExecution).where(VisitExecution.scheduled_visit_id == visit_id))


def log_activity(
    session: Session,
    visit_id: int,
    actor: Actor,
    activity: A,
    status: C,
    notes: str | None = None,
    flagged: bool = False,
) -> CareActivityLog:
    """Record (or update) one activity for an in-progress visit by its assigned caregiver.

    One row per activity type per visit (``OTHER`` may repeat). NOT_RECORDED is
    reserved for the system at check-out.
    """
    visit = session.get(Visit, visit_id)
    if visit is None:
        raise CareLogError("Visit not found.")
    if actor.role != "caregiver" or actor.id != visit.caregiver_id:
        raise AuthorizationError("Only the assigned caregiver can record care for this visit.")
    execution = _execution(session, visit_id)
    if execution is None or execution.execution_status != ExecutionStatus.IN_PROGRESS:
        raise CareLogError("Start the visit (check in) before recording care activities.")
    if status == C.NOT_RECORDED:
        raise CareLogError("Choose Completed, Not completed or Skipped.")
    notes = (notes or "").strip() or None
    if notes and len(notes) > MAX_NOTE:
        raise CareLogError(f"Notes are limited to {MAX_NOTE} characters.")
    if status in (C.NOT_COMPLETED, C.SKIPPED) and not notes:
        raise CareLogError("Please add a short factual note explaining why it was not done.")
    if activity in (A.WELLNESS_OBSERVATION, A.OTHER) and status == C.COMPLETED and not notes:
        raise CareLogError("Please describe what you observed or did.")

    row = None
    if activity != A.OTHER:
        row = session.scalar(select(CareActivityLog).where(
            CareActivityLog.scheduled_visit_id == visit_id, CareActivityLog.activity_type == activity))
    if row is None:
        row = CareActivityLog(scheduled_visit_id=visit_id, caregiver_id=actor.id, activity_type=activity)
        session.add(row)
    row.completion_status, row.notes, row.flagged, row.recorded_at = status, notes, bool(flagged), utc_now()
    session.flush()
    record(session, "visit", visit_id, "activity_logged", actor,
           activity=activity.value, status=status.value, flagged=bool(flagged))
    return row


def fill_unrecorded_essentials(session: Session, visit: Visit) -> list[CareActivityLog]:
    """At check-out: explicitly store every essential activity that has no log."""
    have = {log.activity_type for log in logs_for_visit(session, visit.id)}
    created = []
    for activity in essential_activities(visit.service_type, visit.booking.pet.species):
        if activity not in have:
            row = CareActivityLog(
                scheduled_visit_id=visit.id, caregiver_id=visit.caregiver_id, activity_type=activity,
                completion_status=C.NOT_RECORDED, notes=None, flagged=False,
            )
            session.add(row)
            created.append(row)
    session.flush()
    return created


def review_reasons(session: Session, visit: Visit) -> list[str]:
    essentials = set(essential_activities(visit.service_type, visit.booking.pet.species))
    reasons = []
    for log in logs_for_visit(session, visit.id):
        label = ACTIVITY_LABELS[log.activity_type]
        if log.activity_type in essentials and log.completion_status != C.COMPLETED:
            reasons.append(f"Essential task {STATUS_LABELS[log.completion_status].lower()}: {label}")
        if log.flagged:
            reasons.append(f"Caregiver flagged: {label}")
    return reasons
