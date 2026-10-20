"""Configurable, deterministic pricing engine.

Prices come only from a ``PriceBook``; the LLM is never asked for, or allowed to
produce, a price. All money maths uses ``Decimal``; quotes are stored in integer
cents with the price-book version that produced them.

    Total = sum(visit prices) + additional service fees
    visit price   = base 30-minute rate for the species (+ 60-minute upgrade)
    service fees  = (number of add-on services) x (fee per visit) x (number of visits)

Override the demo rates by pointing ``PAWPAL_PRICE_BOOK`` at a JSON file shaped
like ``DEMO_PRICE_BOOK.to_dict()``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

from sqlalchemy import update
from sqlalchemy.orm import Session

from .models import ALLOWED_VISIT_MINUTES, Booking, BookingQuote

CENT = Decimal("0.01")


class PricingError(Exception):
    """Raised when a booking cannot be priced from the published price book."""


def money(value: Decimal | str | int) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def to_cents(value: Decimal) -> int:
    return int((money(value) * 100).to_integral_value())


def from_cents(cents: int) -> Decimal:
    return money(Decimal(cents) / 100)


def fmt(value: Decimal) -> str:
    return f"${money(value):,.2f}"


@dataclass(frozen=True)
class PriceBook:
    version: str
    base_30min: dict[str, Decimal]          # species -> price of a 30-minute visit
    upgrade_60min: Decimal                  # added to a visit when it lasts 60 minutes
    additional_service_fee: Decimal         # per add-on service, per visit
    additional_services: dict[str, str]     # code -> label
    currency: str = "USD"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "currency": self.currency,
            "base_30min": {k: str(v) for k, v in self.base_30min.items()},
            "upgrade_60min": str(self.upgrade_60min),
            "additional_service_fee": str(self.additional_service_fee),
            "additional_services": dict(self.additional_services),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PriceBook":
        return cls(
            version=str(data["version"]),
            currency=data.get("currency", "USD"),
            base_30min={k.lower(): money(v) for k, v in data["base_30min"].items()},
            upgrade_60min=money(data["upgrade_60min"]),
            additional_service_fee=money(data["additional_service_fee"]),
            additional_services=dict(data["additional_services"]),
        )


DEMO_PRICE_BOOK = PriceBook(
    version="2026-10-demo-v1",
    base_30min={"cat": money("30"), "dog": money("35")},
    upgrade_60min=money("15"),
    additional_service_fee=money("10"),
    additional_services={
        "medication": "Medication administration",
        "litter_box": "Litter box refresh",
        "extra_play": "Extra playtime & enrichment",
        "brushing": "Brushing / light grooming",
        "plant_watering": "Plant watering",
    },
)


def active_price_book() -> PriceBook:
    path = os.getenv("PAWPAL_PRICE_BOOK")
    if not path:
        return DEMO_PRICE_BOOK
    with open(path, encoding="utf-8") as fh:
        return PriceBook.from_dict(json.load(fh))


@dataclass(frozen=True)
class LineItem:
    code: str
    description: str
    quantity: int
    unit_price: Decimal
    amount: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "description": self.description,
            "quantity": self.quantity,
            "unit_price": str(self.unit_price),
            "amount": str(self.amount),
        }


@dataclass(frozen=True)
class Quote:
    pricing_version: str
    currency: str
    line_items: list[LineItem]
    visit_subtotal: Decimal
    additional_fees: Decimal
    total: Decimal
    inputs: dict[str, Any] = field(default_factory=dict)


def visit_price(species: str, duration_minutes: int, book: PriceBook = DEMO_PRICE_BOOK) -> Decimal:
    key = (species or "").strip().lower()
    if key not in book.base_30min:
        raise PricingError(
            f"There is no published rate for a {species or 'pet'} visit yet. "
            "Please contact the concierge desk for a custom quote."
        )
    if duration_minutes not in ALLOWED_VISIT_MINUTES:
        raise PricingError("Visits must be 30 or 60 minutes.")
    price = book.base_30min[key]
    if duration_minutes == 60:
        price += book.upgrade_60min
    return money(price)


def calculate_quote(
    species: str,
    visit_durations: Iterable[int],
    additional_services: Iterable[str] = (),
    book: PriceBook = DEMO_PRICE_BOOK,
) -> Quote:
    durations = list(visit_durations)
    if not durations:
        raise PricingError("There are no visits to price.")
    addons = sorted(set(additional_services))
    unknown = [a for a in addons if a not in book.additional_services]
    if unknown:
        raise PricingError(f"Unknown additional service: {', '.join(unknown)}.")

    key = species.strip().lower()
    items: list[LineItem] = []
    visit_subtotal = Decimal("0")
    base = visit_price(key, 30, book)
    n_visits = len(durations)
    amount = money(base * n_visits)
    items.append(LineItem(f"base_{key}_30", f"{key.capitalize()} visit · 30 min", n_visits, base, amount))
    visit_subtotal += amount

    n_long = sum(1 for d in durations if d == 60)
    for d in durations:
        visit_price(key, d, book)  # validates every duration
    if n_long:
        amount = money(book.upgrade_60min * n_long)
        items.append(LineItem("upgrade_60", "Upgrade to 60 min", n_long, book.upgrade_60min, amount))
        visit_subtotal += amount

    additional_fees = Decimal("0")
    for code in addons:
        amount = money(book.additional_service_fee * n_visits)
        items.append(
            LineItem(f"addon_{code}", book.additional_services[code], n_visits, book.additional_service_fee, amount)
        )
        additional_fees += amount

    return Quote(
        pricing_version=book.version,
        currency=book.currency,
        line_items=items,
        visit_subtotal=money(visit_subtotal),
        additional_fees=money(additional_fees),
        total=money(visit_subtotal + additional_fees),
        inputs={
            "species": key,
            "visits": n_visits,
            "visits_60min": n_long,
            "additional_services": addons,
        },
    )


def quote_for_booking(booking: Booking, book: PriceBook | None = None) -> Quote:
    book = book or active_price_book()
    active_visits = [v for v in booking.visits if v.assignment_status.value != "cancelled"]
    if not active_visits:
        raise PricingError("Generate the visit schedule before pricing.")
    return calculate_quote(
        booking.pet.species,
        [v.duration_minutes for v in active_visits],
        booking.additional_services or [],
        book,
    )


def save_quote(session: Session, booking: Booking, quote: Quote) -> BookingQuote:
    """Store a new current quote; older quotes are kept but marked not current."""
    session.execute(
        update(BookingQuote)
        .where(BookingQuote.booking_id == booking.id, BookingQuote.is_current.is_(True))
        .values(is_current=False)
    )
    for q in booking.quotes:
        q.is_current = False
    row = BookingQuote(
        booking_id=booking.id,
        pricing_version=quote.pricing_version,
        currency=quote.currency,
        visit_subtotal_cents=to_cents(quote.visit_subtotal),
        additional_fees_cents=to_cents(quote.additional_fees),
        total_cents=to_cents(quote.total),
        line_items=[li.to_dict() for li in quote.line_items],
        inputs=quote.inputs,
        is_current=True,
    )
    booking.quotes.append(row)
    session.flush()
    return row


def quote_rows(quote_row: BookingQuote) -> list[dict[str, str]]:
    """Display-ready rows for a stored quote."""
    rows = [
        {
            "Item": li["description"],
            "Qty": str(li["quantity"]),
            "Unit price": fmt(Decimal(li["unit_price"])),
            "Amount": fmt(Decimal(li["amount"])),
        }
        for li in quote_row.line_items
    ]
    rows.append({"Item": "Total", "Qty": "", "Unit price": "", "Amount": fmt(from_cents(quote_row.total_cents))})
    return rows
