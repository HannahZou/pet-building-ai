from datetime import date

import pytest
from pydantic import ValidationError

from pawpal.ai_extraction import ExtractionError, extract_booking
from pawpal.booking_service import review_extraction
from pawpal.models import ServiceType
from pawpal.schemas import ExtractedBooking
from tests.conftest import FakeOpenAI

TODAY = date(2026, 10, 12)
TEXT = "I'm traveling from November 6 to November 9, 2026. My cat Luna needs feeding twice a day and I'd like a photo after every visit."


def full():
    return ExtractedBooking(
        pet_name="Luna", start_date=date(2026, 11, 6), end_date=date(2026, 11, 9),
        visits_per_day=2, service_type=ServiceType.FEEDING, photo_updates_required=True,
    )


def test_schema_rejects_invalid_values():
    with pytest.raises(ValidationError):
        ExtractedBooking(service_type="grooming")
    with pytest.raises(ValidationError):
        ExtractedBooking(start_date="not a date")


def test_extraction_with_mocked_openai():
    client = FakeOpenAI(parsed=full())
    out = extract_booking(TEXT, ["Luna"], today=TODAY, client=client)
    assert out.pet_name == "Luna" and out.visits_per_day == 2
    assert review_extraction(out, "Luna", TODAY).ok
    prompt = client.calls[0]["messages"][0]["content"]
    assert "2026-10-12" in prompt


def test_missing_information_is_flagged_not_invented():
    partial = ExtractedBooking(pet_name="Luna", visits_per_day=2, clarification_questions=["What dates?"])
    out = extract_booking(TEXT, ["Luna"], today=TODAY, client=FakeOpenAI(parsed=partial))
    result = review_extraction(out, "Luna", TODAY)
    assert not result.ok
    assert {"start_date", "end_date", "service_type", "photo_updates_required"} <= set(result.missing_fields)
    assert out.start_date is None and out.end_date is None


def test_invalid_date_range_detected():
    bad = full().model_copy(update={"start_date": date(2026, 11, 9), "end_date": date(2026, 11, 6)})
    result = review_extraction(bad, "Luna", TODAY)
    assert any("earlier than the start" in e for e in result.errors)


def test_pet_name_mismatch_is_blocking():
    result = review_extraction(full(), "Rex", TODAY)
    assert not result.ok and "Rex" in result.errors[0]


def test_hallucinated_pet_name_is_dropped():
    hallucinated = full().model_copy(update={"pet_name": "Fluffy"})
    out = extract_booking(TEXT, ["Luna"], today=TODAY, client=FakeOpenAI(parsed=hallucinated))
    assert out.pet_name is None


def test_empty_request_and_api_failures():
    with pytest.raises(ExtractionError):
        extract_booking("   ", client=FakeOpenAI())
    with pytest.raises(ExtractionError):
        extract_booking(TEXT, client=FakeOpenAI(raises=RuntimeError("boom")))
    with pytest.raises(ExtractionError):
        extract_booking(TEXT, client=FakeOpenAI(refusal="no"))
    with pytest.raises(ExtractionError):
        extract_booking(TEXT, client=FakeOpenAI(parsed=None))


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ExtractionError):
        extract_booking(TEXT)
