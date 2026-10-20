import json
from datetime import time
from decimal import Decimal

import pytest

from pawpal.pricing import (
    DEMO_PRICE_BOOK,
    PricingError,
    active_price_book,
    calculate_quote,
    quote_for_booking,
    save_quote,
    visit_price,
)
from pawpal.scheduling import Window, generate_visits, set_time_windows
from tests import factories as f


def test_demo_rates():
    assert visit_price("cat", 30) == Decimal("30.00")
    assert visit_price("Dog", 30) == Decimal("35.00")
    assert visit_price("cat", 60) == Decimal("45.00")
    assert visit_price("dog", 60) == Decimal("50.00")


def test_itemised_quote_uses_decimal():
    q = calculate_quote("cat", [30] * 8, ["medication"])
    assert q.total == Decimal("320.00") and isinstance(q.total, Decimal)
    assert [(li.code, li.quantity, li.amount) for li in q.line_items] == [
        ("base_cat_30", 8, Decimal("240.00")), ("addon_medication", 8, Decimal("80.00"))]
    assert q.visit_subtotal + q.additional_fees == q.total
    assert q.pricing_version == DEMO_PRICE_BOOK.version


def test_upgrade_and_multiple_addons():
    q = calculate_quote("dog", [60, 60, 30], ["extra_play", "brushing"])
    # visits: 3*35 + 2*15 = 135 ; add-ons: 2 services * 3 visits * 10 = 60
    assert (q.visit_subtotal, q.additional_fees, q.total) == (Decimal("135.00"), Decimal("60.00"), Decimal("195.00"))


def test_no_invented_prices():
    with pytest.raises(PricingError, match="no published rate"):
        calculate_quote("rabbit", [30])
    with pytest.raises(PricingError):
        calculate_quote("cat", [45])
    with pytest.raises(PricingError):
        calculate_quote("cat", [30], ["spa_day"])
    with pytest.raises(PricingError):
        calculate_quote("cat", [])


def test_price_book_is_configurable(tmp_path, monkeypatch):
    data = DEMO_PRICE_BOOK.to_dict() | {"version": "custom-v2", "base_30min": {"cat": "32.50", "dog": "35"}}
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("PAWPAL_PRICE_BOOK", str(path))
    book = active_price_book()
    q = calculate_quote("cat", [30, 30, 30], book=book)
    assert q.total == Decimal("97.50") and q.pricing_version == "custom-v2"


def test_quote_saved_and_recalculated(session):
    r = f.resident(session)
    p = f.pet(session, r)
    b = f.booking(session, r, p)
    set_time_windows(session, b, [Window(time(7), time(10)), Window(time(17), time(20))])
    generate_visits(session, b)
    first = save_quote(session, b, quote_for_booking(b))
    assert (first.total_cents, first.pricing_version, first.is_current) == (24000, DEMO_PRICE_BOOK.version, True)

    b.visit_duration_minutes = 60
    b.additional_services = ["litter_box"]
    generate_visits(session, b)
    second = save_quote(session, b, quote_for_booking(b))
    session.refresh(first)
    assert second.total_cents == 8 * 4500 + 8 * 1000
    assert first.is_current is False and b.current_quote.id == second.id
    assert second.line_items[-1]["description"] == "Litter box refresh"
