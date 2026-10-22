"""Role-based access for analytics, evaluation and exports (Week 4).

Enforced in the service layer: every analytics/report function takes an
``Actor`` and calls ``analytics_scope`` first. Hiding Streamlit widgets is never
the only protection.

| Role             | Analytics scope                                   |
|------------------|---------------------------------------------------|
| resident         | none (own records only, via the Week 1-3 services) |
| caregiver        | none (own assignments only, via Week 2-3 services) |
| property_manager | the building(s) they manage (NULL building = all)  |
| platform_admin   | only what their explicit permissions grant         |

Platform-admin permissions: ``analytics:read``, ``evaluation:read``,
``evaluation:run``, ``reports:export``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .actors import Actor, AuthorizationError
from .models import PlatformAdmin, PropertyManager, Resident

PERMISSIONS = ("analytics:read", "evaluation:read", "evaluation:run", "reports:export")


@dataclass(frozen=True)
class Scope:
    role: str
    actor_id: int | None
    buildings: frozenset[str] | None  # None = every building
    can_export: bool
    can_read_evaluation: bool
    can_run_evaluation: bool

    def allows(self, building: str) -> bool:
        return self.buildings is None or building in self.buildings


def all_buildings(session: Session) -> list[str]:
    return sorted(set(session.scalars(select(Resident.building_name))))


def analytics_scope(session: Session, actor: Actor, requested: Iterable[str] | None = None) -> Scope:
    """Return what ``actor`` may see; narrow it to ``requested`` buildings if given.

    Raises AuthorizationError for roles without analytics access, unknown actors,
    or a request for a building outside the actor's scope.
    """
    if actor.role == "property_manager":
        manager = session.get(PropertyManager, actor.id)
        if manager is None:
            raise AuthorizationError("Unknown property manager.")
        allowed = None if manager.building_name is None else frozenset({manager.building_name})
        scope = Scope("property_manager", actor.id, allowed, True, True, False)
    elif actor.role == "platform_admin":
        admin = session.get(PlatformAdmin, actor.id)
        perms = set(admin.permissions or []) if admin else set()
        if admin is None or "analytics:read" not in perms:
            raise AuthorizationError("This administrator account has no analytics permission.")
        scope = Scope("platform_admin", actor.id, None, "reports:export" in perms,
                      "evaluation:read" in perms, "evaluation:run" in perms)
    else:
        raise AuthorizationError("Analytics are available to property managers and authorised administrators only.")

    if requested:
        wanted = frozenset(requested)
        if scope.buildings is not None and not wanted <= scope.buildings:
            raise AuthorizationError("You can only view analytics for buildings you manage.")
        scope = Scope(scope.role, scope.actor_id, wanted, scope.can_export,
                      scope.can_read_evaluation, scope.can_run_evaluation)
    return scope


def require(scope: Scope, flag: str) -> None:
    if not getattr(scope, flag):
        raise AuthorizationError("You do not have permission for this action.")
