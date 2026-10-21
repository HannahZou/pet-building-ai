"""Generic audit trail. Store ids and statuses only - never notes, captions or medical data."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .actors import Actor
from .models import AuditEvent


def record(session: Session, entity_type: str, entity_id: int, action: str, actor: Actor, **details) -> AuditEvent:
    event = AuditEvent(
        entity_type=entity_type, entity_id=entity_id, action=action,
        actor_role=actor.role, actor_id=actor.id, details=details,
    )
    session.add(event)
    session.flush()
    return event


def events_for(session: Session, entity_type: str, entity_id: int) -> list[AuditEvent]:
    return list(session.scalars(
        select(AuditEvent)
        .where(AuditEvent.entity_type == entity_type, AuditEvent.entity_id == entity_id)
        .order_by(AuditEvent.id)
    ))
