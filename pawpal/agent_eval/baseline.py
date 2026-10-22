"""A transparent rule-based booking extractor - the *baseline* for the benchmark.

It is not part of the app. It exists so the evaluation pipeline can run without
an API key and so the LLM has a simple reference to beat. Its results are always
labelled ``rules-baseline-v1``, never as the LLM's.
"""
from __future__ import annotations

import re
from datetime import date

from ..models import ServiceType
from ..schemas import ExtractedBooking, ExtractedTimeWindow

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

UNSUPPORTED = re.compile(r"\b(groom|nails|vet|bath|train|boarding|daycare|insulin|inject|clean the)\w*", re.I)
SERVICE_WORDS = [
    (ServiceType.FULL_CARE, re.compile(r"\bfull care\b", re.I)),
    (ServiceType.CHECK_IN, re.compile(r"\bcheck[- ]?(in|on)\b", re.I)),
    (ServiceType.WALKING, re.compile(r"\bwalk(s|ing)?\b", re.I)),
    (ServiceType.FEEDING, re.compile(r"\bfeed(ing)?\b", re.I)),
]
VPD_WORDS = [(3, r"three times|3 times"), (2, r"twice|two visits|2 times|two times|morning and evening|breakfast and dinner"),
             (1, r"\bonce\b|\bdaily\b|one visit")]
PHOTO = re.compile(r"\b(photo|picture|pic|video|updates?)\w*", re.I)
NEG = re.compile(r"\b(no|don'?t|not|aren'?t|skip|no need)\b", re.I)


def _year_for(month: int, day: int, today: date) -> int:
    candidate = date(today.year, month, day)
    return today.year if candidate >= today else today.year + 1


def _dates(text: str, today: date) -> list[date] | None:
    """Ordered dates mentioned in the text; None when a numeric date is ambiguous."""
    if re.search(r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b", text):
        return None
    found: list[tuple[int, date | tuple]] = []
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        found.append((m.start(), date(int(m[1]), int(m[2]), int(m[3]))))
    pattern = re.compile(MONTH_RE + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*(?:-|to|through)\s*(?:" + MONTH_RE + r"\s+)?(\d{1,2}))?,?\s*(\d{4})?", re.I)
    for m in pattern.finditer(text):
        month = MONTHS[m[1].lower()[:3]]
        day1, month2, day2, year = int(m[2]), m[3], m[4], m[5]
        found.append((m.start(), (month, day1, int(year) if year else None)))
        if day2:
            found.append((m.start() + 1, (MONTHS[month2.lower()[:3]] if month2 else month, int(day2), int(year) if year else None)))
    found.sort(key=lambda x: x[0])
    explicit_year = next((v[2] for _, v in found if isinstance(v, tuple) and v[2]), None)
    out = []
    for _, v in found:
        if isinstance(v, date):
            out.append(v)
            continue
        month, day, year = v
        try:
            out.append(date(year or explicit_year or _year_for(month, day, today), month, day))
        except ValueError:
            continue
    return out


def _times(text: str) -> list[ExtractedTimeWindow]:
    out = []
    for m in re.finditer(r"between (\d{1,2})(?::(\d{2}))? and (\d{1,2})(?::(\d{2}))?\s*([ap]m)", text, re.I):
        pm = m[5].lower() == "pm"
        h1, h2 = int(m[1]) + (12 if pm and int(m[1]) < 12 else 0), int(m[3]) + (12 if pm and int(m[3]) < 12 else 0)
        out.append((m.start(), ExtractedTimeWindow(start=f"{h1:02d}:{int(m[2] or 0):02d}", end=f"{h2:02d}:{int(m[4] or 0):02d}")))
    spans = [range(m.start(), m.end()) for m in re.finditer(r"between [^,.]*?[ap]m", text, re.I)]
    for m in re.finditer(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b|\bat (\d{1,2}):(\d{2})\b|\band (\d{1,2}):(\d{2})\b", text, re.I):
        if any(m.start() in s for s in spans):
            continue
        if m[3]:
            h = int(m[1]) % 12 + (12 if m[3].lower() == "pm" else 0)
            mm = int(m[2] or 0)
        else:
            h, mm = (int(m[4]), int(m[5])) if m[4] else (int(m[6]), int(m[7]))
        out.append((m.start(), ExtractedTimeWindow(start=f"{h:02d}:{mm:02d}")))
    return [w for _, w in sorted(out, key=lambda x: x[0])]


def extract(text: str, pet_names: list[str], today: date) -> ExtractedBooking:
    pets = [p for p in pet_names if re.search(rf"\b{re.escape(p)}\b", text)]
    dates = _dates(text, today)
    start = end = None
    if dates:
        start = dates[0]
        end = dates[1] if len(dates) > 1 else (dates[0] if re.search(r"\bon\b", text) else None)
    vpds = {n for n, pat in VPD_WORDS if re.search(pat, text, re.I)}
    vpd = max(vpds) if len(vpds) == 1 or (vpds and 1 in vpds and len(vpds) == 2 and re.search(r"daily", text, re.I)) else None
    if vpds == {2, 1} and re.search(r"twice daily|two visits daily", text, re.I):
        vpd = 2
    if UNSUPPORTED.search(text):
        service = None
    else:
        services = {s for s, pat in SERVICE_WORDS if pat.search(text)}
        if ServiceType.FULL_CARE in services:
            services = {ServiceType.FULL_CARE}
        service = services.pop() if len(services) == 1 else None
    photo = None
    mentions = [s for s in re.split(r"(?<=[.,;!?])\s+", text) if PHOTO.search(s)]
    if mentions:
        flags = {not NEG.search(s) for s in mentions}
        photo = flags.pop() if len(flags) == 1 else None
    sentences = [s.strip() for s in re.split(r"(?<=[.;])\s+", text)]
    instructions = [s for s in sentences[1:] if not PHOTO.search(s) and not re.search(MONTH_RE + r"\s+\d", s, re.I)]
    return ExtractedBooking(
        pet_name=pets[0] if len(pets) == 1 else None, start_date=start, end_date=end, visits_per_day=vpd,
        service_type=service, photo_updates_required=photo,
        special_instructions=" ".join(instructions) or None, time_windows=_times(text),
    )
