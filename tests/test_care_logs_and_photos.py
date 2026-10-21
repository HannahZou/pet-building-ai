import io

import pytest
from PIL import Image

from pawpal.actors import Actor, AuthorizationError
from pawpal.care_logs import CareLogError, essential_activities, log_activity, logs_for_visit
from pawpal.database import session_scope
from pawpal.models import ActivityType as A, CompletionStatus as C, ExecutionStatus as E, PhotoVisibility, ServiceType
from pawpal.photo_storage import LocalPhotoStorage, PhotoValidationError, sanitize_image
from pawpal.photos import photos_for_resident, read_photo, set_visibility, upload_photo
from pawpal.visit_execution import check_in, check_out
from tests import factories as f
from tests.week3_world import DAY1, at, confirmed_world, jpeg_bytes, png_bytes


# ------------------------------------------------------------ activity logs
def test_essential_activities_by_service_and_species():
    assert essential_activities(ServiceType.FEEDING, "cat") == [A.FEEDING, A.FRESH_WATER]
    assert essential_activities(ServiceType.WALKING, "dog") == [A.DOG_WALKING, A.FRESH_WATER]
    assert essential_activities(ServiceType.FULL_CARE, "cat")[-1] == A.LITTER_BOX
    assert essential_activities(ServiceType.FULL_CARE, "dog")[-1] == A.DOG_WALKING
    assert essential_activities(ServiceType.CHECK_IN, "cat") == [A.WELLNESS_OBSERVATION]


def test_activity_logging_rules(file_engine):
    w = confirmed_world(file_engine)
    v = w.visits[DAY1][0]
    with pytest.raises(CareLogError, match="check in"):
        with session_scope(file_engine) as s:
            log_activity(s, v, w.caregiver, A.FEEDING, C.COMPLETED)
    with session_scope(file_engine) as s:
        check_in(s, v, w.caregiver, now=at(DAY1, 7))
    with pytest.raises(AuthorizationError):
        with session_scope(file_engine) as s:
            log_activity(s, v, w.res_actor, A.FEEDING, C.COMPLETED)
    with pytest.raises(CareLogError, match="why"):
        with session_scope(file_engine) as s:
            log_activity(s, v, w.caregiver, A.FEEDING, C.NOT_COMPLETED)  # needs a reason
    with pytest.raises(CareLogError):
        with session_scope(file_engine) as s:
            log_activity(s, v, w.caregiver, A.FEEDING, C.NOT_RECORDED)  # system-only status
    with session_scope(file_engine) as s:
        log_activity(s, v, w.caregiver, A.FEEDING, C.NOT_COMPLETED, "Bowl untouched from last night")
        log_activity(s, v, w.caregiver, A.FEEDING, C.COMPLETED, "Fresh food; ate half")  # correction: same row
        log_activity(s, v, w.caregiver, A.WELLNESS_OBSERVATION, C.COMPLETED, "Hid under the bed", flagged=True)
        logs = logs_for_visit(s, v)
        assert [(l.activity_type, l.completion_status) for l in logs] == [(A.FEEDING, C.COMPLETED), (A.WELLNESS_OBSERVATION, C.COMPLETED)]


def test_check_out_stores_unrecorded_essentials_explicitly(file_engine):
    w = confirmed_world(file_engine)
    v = w.visits[DAY1][0]
    with session_scope(file_engine) as s:
        check_in(s, v, w.caregiver, now=at(DAY1, 7))
        log_activity(s, v, w.caregiver, A.FEEDING, C.COMPLETED)
        log_activity(s, v, w.caregiver, A.PLAYTIME, C.COMPLETED)
    with session_scope(file_engine) as s:
        res = check_out(s, v, w.caregiver, now=at(DAY1, 7, 30))
        assert res.execution.execution_status == E.NEEDS_REVIEW
        assert res.reasons == ["Essential task not recorded: Fresh water"]
        water = [l for l in logs_for_visit(s, v) if l.activity_type == A.FRESH_WATER][0]
        assert water.completion_status == C.NOT_RECORDED  # never assumed completed


def test_flagged_observation_needs_review(file_engine):
    w = confirmed_world(file_engine)
    v = w.visits[DAY1][0]
    with session_scope(file_engine) as s:
        check_in(s, v, w.caregiver, now=at(DAY1, 7))
        log_activity(s, v, w.caregiver, A.FEEDING, C.COMPLETED)
        log_activity(s, v, w.caregiver, A.FRESH_WATER, C.COMPLETED)
        log_activity(s, v, w.caregiver, A.WELLNESS_OBSERVATION, C.COMPLETED, "Did not come out to greet me", flagged=True)
        res = check_out(s, v, w.caregiver, now=at(DAY1, 7, 30))
    assert res.needs_review and res.reasons == ["Caregiver flagged: Wellness observation"]


# -------------------------------------------------------------- validation
def test_photo_validation():
    assert sanitize_image(jpeg_bytes(), "a.JPG").content_type == "image/jpeg"
    assert sanitize_image(jpeg_bytes(), "a.jpeg").ext == "jpg"
    assert sanitize_image(png_bytes(), "a.png").content_type == "image/png"
    with pytest.raises(PhotoValidationError, match="Only JPG"):
        sanitize_image(jpeg_bytes(), "a.gif")
    with pytest.raises(PhotoValidationError, match="not a valid image"):
        sanitize_image(b"this is not an image" * 10, "a.jpg")
    with pytest.raises(PhotoValidationError, match="does not match"):
        sanitize_image(png_bytes(), "a.jpg")
    with pytest.raises(PhotoValidationError, match="at most"):
        sanitize_image(jpeg_bytes(size=(400, 400)), "a.jpg", max_bytes=500)
    with pytest.raises(PhotoValidationError, match="empty"):
        sanitize_image(b"", "a.png")


def test_size_limit_is_configurable(monkeypatch):
    monkeypatch.setenv("PAWPAL_MAX_PHOTO_MB", "0.0001")  # ~100 bytes
    with pytest.raises(PhotoValidationError, match="at most"):
        sanitize_image(jpeg_bytes(), "a.jpg")


def test_exif_metadata_is_removed():
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"       # Make
    exif[0x013B] = "Owner Full Name"         # Artist
    exif[0x0132] = "2026:11:06 07:05:00"     # DateTime
    original = jpeg_bytes(exif=exif.tobytes())
    with Image.open(io.BytesIO(original)) as img:
        assert img.getexif().get(0x010F) == "SecretCameraMaker"
    clean = sanitize_image(original, "pet.jpg").data
    with Image.open(io.BytesIO(clean)) as img:
        assert dict(img.getexif()) == {}
    assert b"SecretCameraMaker" not in clean


def test_storage_keys_are_unique_and_safe(tmp_path):
    store = LocalPhotoStorage(tmp_path)
    k1, k2 = store.save(b"a", "jpg"), store.save(b"a", "jpg")
    assert k1 != k2 and store.read(k1) == b"a"
    with pytest.raises(PhotoValidationError):
        store.read("../../etc/passwd")


# ------------------------------------------------------- upload + access
def _uploaded(engine):
    w = confirmed_world(engine)
    v = w.visits[DAY1][0]
    with session_scope(engine) as s:
        check_in(s, v, w.caregiver, now=at(DAY1, 7))
        photo = upload_photo(s, v, w.caregiver, "luna.jpg", jpeg_bytes(), "Luna by the window")
        w.photo, w.photo_key = photo.id, photo.file_path
    return w


def test_photo_upload_rules(file_engine, tmp_path):
    w = _uploaded(file_engine)
    assert (tmp_path / "photos" / w.photo_key).exists() and "luna" not in w.photo_key  # random name
    v_later = w.visits[DAY1][1]
    with pytest.raises(PhotoValidationError, match="in progress"):
        with session_scope(file_engine) as s:
            upload_photo(s, v_later, w.caregiver, "x.jpg", jpeg_bytes())
    with pytest.raises(AuthorizationError):
        with session_scope(file_engine) as s:
            upload_photo(s, w.visits[DAY1][0], w.res_actor, "x.jpg", jpeg_bytes())


def test_photo_authorization(file_engine):
    w = _uploaded(file_engine)
    with session_scope(file_engine) as s:
        other_cg = f.caregiver(s, name="Other Carer", days=None).id
    allowed = [w.res_actor, w.caregiver, w.admin_actor]
    denied = [Actor.resident(w.other_resident), Actor.caregiver(other_cg), Actor.manager(w.remote)]
    with session_scope(file_engine) as s:
        for actor in allowed:
            assert read_photo(s, w.photo, actor).startswith(b"\xff\xd8")  # JPEG bytes, no path
        for actor in denied:
            with pytest.raises(AuthorizationError):
                read_photo(s, w.photo, actor)
        assert [p.id for p in photos_for_resident(s, w.res_actor)] == [w.photo]
        assert photos_for_resident(s, Actor.resident(w.other_resident)) == []
    with pytest.raises(AuthorizationError):
        with session_scope(file_engine) as s:
            set_visibility(s, w.photo, w.res_actor, PhotoVisibility.HIDDEN)
    with session_scope(file_engine) as s:
        set_visibility(s, w.photo, w.admin_actor, PhotoVisibility.HIDDEN)
    with session_scope(file_engine) as s:
        with pytest.raises(AuthorizationError):
            read_photo(s, w.photo, w.res_actor)            # hidden from the resident
        assert read_photo(s, w.photo, w.admin_actor)       # managers still see it
