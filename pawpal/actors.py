"""Who is acting. Every write in Week 2 takes an ``Actor`` and records it.

The Streamlit demo picks the actor from a dropdown (no login yet); the service
layer still enforces what each role may do, so adding real authentication later
only changes how the ``Actor`` is built.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Role = Literal["resident", "caregiver", "property_manager", "system"]


class AuthorizationError(Exception):
    """The actor is not allowed to perform this action."""


@dataclass(frozen=True)
class Actor:
    role: Role
    id: int | None = None

    @classmethod
    def resident(cls, resident_id: int) -> "Actor":
        return cls("resident", resident_id)

    @classmethod
    def caregiver(cls, caregiver_id: int) -> "Actor":
        return cls("caregiver", caregiver_id)

    @classmethod
    def manager(cls, manager_id: int) -> "Actor":
        return cls("property_manager", manager_id)


SYSTEM = Actor("system", None)
