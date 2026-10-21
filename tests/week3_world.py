"""A confirmed booking (via the real Week 2 workflow) for Week 3 tests."""
from __future__ import annotations

import io
from datetime import date, datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from PIL import Image

from pawpal.actors import Actor
from pawpal.database import session_scope
from pawpal.models import Booking, ServiceType
from pawpal.scheduling import Window
from pawpal.workflow import confirm_booking, prepare_booking, resident_approve
from tests import factories as f

NY = ZoneInfo("America/New_York")
DAY1, DAY2 = date(2026, 11, 6), date(2026, 11, 7)


def at(day: date, h: int, m: int = 0, tz=NY) -> datetime:
    return datetime.combine(day, time(h, m), tzinfo=tz)


def jpeg_bytes(size=(64, 48), color=(200, 120, 90), exif: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, color)
    if exif is not None:
        img.save(buf, "JPEG", exif=exif)
    else:
        img.save(buf, "JPEG")
    return buf.getvalue()


def png_bytes(size=(40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, (10, 200, 10, 255)).save(buf, "PNG")
    return buf.getvalue()


def confirmed_world(engine, species="cat", service=ServiceType.FEEDING, building=f.B1, confirm=True):
    with session_scope(engine) as s:
        cg = f.caregiver(s, name="Maya Carer", buildings=(building,))
        r = f.resident(s, building=building)
        other_res = f.resident(s, building=building, name="Other Resident")
        p = f.pet(s, r, species=species, name="Luna" if species == "cat" else "Rex")
        p.medical_notes = "SECRET-MEDICAL-NOTE"
        admin = f.manager(s)
        remote = f.manager(s, building="Skyline Court", name="Remote Manager")
        b = f.booking(s, r, p, start=DAY1, end=DAY2, visits_per_day=2, service=service)
        ids = SimpleNamespace(cg=cg.id, resident=r.id, other_resident=other_res.id, pet=p.id,
                              admin=admin.id, remote=remote.id, booking=b.id)
    with session_scope(engine) as s:
        prepare_booking(s, ids.booking, Actor.resident(ids.resident), windows=[Window(time(7), time(10)), Window(time(17), time(20))])
    with session_scope(engine) as s:
        resident_approve(s, ids.booking, Actor.resident(ids.resident))
    if confirm:
        assert confirm_booking(ids.booking, Actor.manager(ids.admin), engine=engine).ok
    with session_scope(engine) as s:
        b = s.get(Booking, ids.booking)
        ids.visits = {d: [v.id for v in b.visits if v.visit_date == d] for d in (DAY1, DAY2)}
    ids.caregiver = Actor.caregiver(ids.cg)
    ids.res_actor = Actor.resident(ids.resident)
    ids.admin_actor = Actor.manager(ids.admin)
    return ids
