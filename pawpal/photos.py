"""Pet-care photos: upload by the assigned caregiver, authorised viewing.

Who may view a photo: the resident who owns the booking (only if visible),
the caregiver assigned to the visit, and property managers of the building.
A photo is evidence that a photo was taken - it is never treated as proof that
a care activity happened, and nothing infers health from it.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .actors import Actor, AuthorizationError
from .audit import record
from .models import Booking, ExecutionStatus, PetCarePhoto, PhotoVisibility, PropertyManager, Visit, VisitExecution
from .photo_storage import PhotoStorage, PhotoValidationError, default_storage, sanitize_image

MAX_CAPTION = 300


def upload_photo(
    session: Session,
    visit_id: int,
    actor: Actor,
    filename: str,
    data: bytes,
    caption: str | None = None,
    storage: PhotoStorage | None = None,
) -> PetCarePhoto:
    storage = storage or default_storage()
    visit = session.get(Visit, visit_id)
    if visit is None:
        raise PhotoValidationError("Visit not found.")
    if actor.role != "caregiver" or actor.id != visit.caregiver_id:
        raise AuthorizationError("Only the caregiver assigned to this visit can upload photos.")
    ex = session.scalar(select(VisitExecution).where(VisitExecution.scheduled_visit_id == visit_id))
    if ex is None or ex.execution_status != ExecutionStatus.IN_PROGRESS:
        raise PhotoValidationError("Photos can be added while the visit is in progress.")
    caption = (caption or "").strip() or None
    if caption and len(caption) > MAX_CAPTION:
        raise PhotoValidationError(f"Captions are limited to {MAX_CAPTION} characters.")

    clean = sanitize_image(data, filename)
    key = storage.save(clean.data, clean.ext)
    try:
        photo = PetCarePhoto(
            scheduled_visit_id=visit_id, caregiver_id=actor.id, file_path=key,
            content_type=clean.content_type, size_bytes=len(clean.data),
            width=clean.width, height=clean.height, caption=caption,
            visibility_status=PhotoVisibility.VISIBLE,
        )
        session.add(photo)
        session.flush()
    except Exception:
        storage.delete(key)  # no orphaned files
        raise
    record(session, "visit", visit_id, "photo_uploaded", actor, photo_id=photo.id)
    return photo


def can_view(session: Session, photo: PetCarePhoto, actor: Actor) -> bool:
    visit = photo.visit
    booking: Booking = visit.booking
    if actor.role == "resident":
        return actor.id == booking.resident_id and photo.visibility_status == PhotoVisibility.VISIBLE
    if actor.role == "caregiver":
        return actor.id == visit.caregiver_id
    if actor.role == "property_manager":
        manager = session.get(PropertyManager, actor.id)
        return bool(manager and manager.manages(booking.resident.building_name))
    return False


def read_photo(session: Session, photo_id: int, actor: Actor, storage: PhotoStorage | None = None) -> bytes:
    photo = session.get(PetCarePhoto, photo_id)
    if photo is None or not can_view(session, photo, actor):
        raise AuthorizationError("You do not have access to this photo.")  # same message: no existence leak
    return (storage or default_storage()).read(photo.file_path)


def photos_for_visit(session: Session, visit_id: int, actor: Actor) -> list[PetCarePhoto]:
    rows = session.scalars(select(PetCarePhoto).where(PetCarePhoto.scheduled_visit_id == visit_id).order_by(PetCarePhoto.id))
    return [p for p in rows if can_view(session, p, actor)]


def photos_for_resident(session: Session, actor: Actor, limit: int = 60) -> list[PetCarePhoto]:
    if actor.role != "resident":
        raise AuthorizationError("Only residents have a photo gallery.")
    return list(session.scalars(
        select(PetCarePhoto)
        .join(Visit, PetCarePhoto.scheduled_visit_id == Visit.id)
        .join(Booking, Visit.booking_id == Booking.id)
        .where(Booking.resident_id == actor.id, PetCarePhoto.visibility_status == PhotoVisibility.VISIBLE)
        .order_by(PetCarePhoto.uploaded_at.desc())
        .limit(limit)
    ))


def set_visibility(session: Session, photo_id: int, actor: Actor, visibility: PhotoVisibility) -> PetCarePhoto:
    photo = session.get(PetCarePhoto, photo_id)
    if photo is None or actor.role != "property_manager" or not can_view(session, photo, actor):
        raise AuthorizationError("Only a manager of this building can change photo visibility.")
    photo.visibility_status = visibility
    session.flush()
    record(session, "photo", photo.id, "visibility_changed", actor, visibility=visibility.value)
    return photo
