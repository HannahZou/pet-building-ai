"""Build the booking-extraction benchmark (hand-written cases and labels).

    python evaluation/build_booking_benchmark.py

Writes ``evaluation/datasets/booking_requests_v1.jsonl``. Every expected output
below was written by hand following the labelling policy in
docs/EVALUATION_METHODOLOGY.md - no LLM generated or graded these labels.
The script only expands a few hand-written phrasings with explicit values, so the
file is reproducible byte for byte.

Labelling policy (mirrors the Week 1 extraction rules):
* A field gets a value only if the request states it unambiguously; otherwise null.
* Dates without a year resolve to the next upcoming occurrence after TODAY.
  Relative phrases ("next weekend", "this Friday") and numeric dates that could
  be day/month or month/day are ambiguous -> null.
* Several pets in one request -> pet_name null (one booking per pet; ask).
* Services other than feeding / walking / check-in / full care -> service_type null.
* Contradictory statements about a field -> that field null.
* Visit times only when clock times are stated; "morning" is not a time.
* ``special_instructions_keywords``: words that must appear in the extracted
  instructions (empty list = no instructions expected).
"""
from __future__ import annotations

import json
from pathlib import Path

TODAY = "2026-10-20"
OUT = Path(__file__).parent / "datasets" / "booking_requests_v1.jsonl"


def case(cid, category, text, pets, *, pet=None, start=None, end=None, vpd=None, service=None, photo=None,
         times=(), keywords=(), invalid_range=False):
    return {
        "id": cid, "category": category, "text": text, "pet_names": pets, "today": TODAY,
        "expected": {
            "pet_name": pet, "start_date": start, "end_date": end, "visits_per_day": vpd,
            "service_type": service, "photo_updates_required": photo,
            "time_windows": [list(t) for t in times], "special_instructions_keywords": list(keywords),
        },
        "expected_invalid_range": invalid_range,
    }


CASES: list[dict] = []
add = CASES.append

# ------------------------------------------------------------------ simple (24)
simple = [
    ("I'm traveling from November 6 to November 9, 2026. My cat Luna needs feeding twice a day and I'd like a photo after every visit.",
     ["Luna"], "Luna", "2026-11-06", "2026-11-09", 2, "feeding", True),
    ("Out of town Dec 20 through Dec 27, 2026. Please walk Max three times a day, no photos needed.",
     ["Max"], "Max", "2026-12-20", "2026-12-27", 3, "walking", False),
    ("Please check in on Milo once a day from November 12 to November 14, 2026. Photos would be great.",
     ["Milo"], "Milo", "2026-11-12", "2026-11-14", 1, "check_in", True),
    ("Need full care for Bella from 2026-11-20 to 2026-11-23, two visits daily, no photo updates please.",
     ["Bella"], "Bella", "2026-11-20", "2026-11-23", 2, "full_care", False),
    ("Feed Oliver once a day while I'm away December 1-3, 2026. Send me photos.",
     ["Oliver"], "Oliver", "2026-12-01", "2026-12-03", 1, "feeding", True),
    ("Walk Daisy twice daily from January 5 to January 8, 2027. I don't need photos.",
     ["Daisy"], "Daisy", "2027-01-05", "2027-01-08", 2, "walking", False),
    ("Coco needs feeding 2 times a day, Nov 15 2026 to Nov 18 2026, with photo updates.",
     ["Coco"], "Coco", "2026-11-15", "2026-11-18", 2, "feeding", True),
    ("Can someone do a daily check-in on Leo from Nov 25 to Nov 28, 2026? No pictures necessary.",
     ["Leo"], "Leo", "2026-11-25", "2026-11-28", 1, "check_in", False),
]
pets_cycle = [("Willow", ["Willow"]), ("Finn", ["Finn"]), ("Nala", ["Nala"])]
n = 0
for text, pets, pet, s, e, vpd, svc, photo in simple:
    n += 1
    add(case(f"simple-{n:03d}", "simple", text, pets, pet=pet, start=s, end=e, vpd=vpd, service=svc, photo=photo))
variants = [
    ("{p} needs feeding twice a day from {s_txt} to {e_txt}. Please send photos.", 2, "feeding", True),
    ("Please walk {p} once a day, {s_txt} through {e_txt}. No photos.", 1, "walking", False),
]
ranges = [("March 3, 2027", "March 6, 2027", "2027-03-03", "2027-03-06"),
          ("February 14, 2027", "February 16, 2027", "2027-02-14", "2027-02-16"),
          ("November 30, 2026", "December 2, 2026", "2026-11-30", "2026-12-02"),
          ("April 10, 2027", "April 12, 2027", "2027-04-10", "2027-04-12"),
          ("December 24, 2026", "December 26, 2026", "2026-12-24", "2026-12-26"),
          ("May 1, 2027", "May 4, 2027", "2027-05-01", "2027-05-04"),
          ("January 15, 2027", "January 18, 2027", "2027-01-15", "2027-01-18"),
          ("June 2, 2027", "June 5, 2027", "2027-06-02", "2027-06-05")]
for i, (s_txt, e_txt, s, e) in enumerate(ranges):
    for tmpl, vpd, svc, photo in variants:
        if n >= 24:
            break
        pet, pets = pets_cycle[(i + vpd) % 3]
        n += 1
        add(case(f"simple-{n:03d}", "simple", tmpl.format(p=pet, s_txt=s_txt, e_txt=e_txt), pets,
                 pet=pet, start=s, end=e, vpd=vpd, service=svc, photo=photo))

# --------------------------------------------------------------- multiple pets (10)
multi = [
    ("Please feed Luna and Milo twice a day from November 6 to November 9, 2026, with photos.", ["Luna", "Milo"], "2026-11-06", "2026-11-09", 2, "feeding", True),
    ("Walk Max and Rex once a day Dec 1-4, 2026. No photos needed.", ["Max", "Rex"], "2026-12-01", "2026-12-04", 1, "walking", False),
    ("My two cats, Pepper and Ginger, need a daily check-in from January 3 to January 6, 2027. Photos please.", ["Pepper", "Ginger"], "2027-01-03", "2027-01-06", 1, "check_in", True),
    ("Bella, Coco and Teddy all need full care twice a day from November 20 to November 22, 2026. No pictures.", ["Bella", "Coco", "Teddy"], "2026-11-20", "2026-11-22", 2, "full_care", False),
    ("Feed both Oliver and Daisy twice daily, Feb 1 to Feb 3, 2027, photo after each visit.", ["Oliver", "Daisy"], "2027-02-01", "2027-02-03", 2, "feeding", True),
    ("Walk Juno and Atlas twice a day from March 10 to March 12, 2027. I'd like photos.", ["Juno", "Atlas"], "2027-03-10", "2027-03-12", 2, "walking", True),
    ("Luna and Nala need feeding once a day from December 5 to December 7, 2026, no photos.", ["Luna", "Nala"], "2026-12-05", "2026-12-07", 1, "feeding", False),
    ("Check on my pets Finn and Hazel daily from April 2 to April 4, 2027. Photos welcome.", ["Finn", "Hazel"], "2027-04-02", "2027-04-04", 1, "check_in", True),
    ("Max and Bruno need walks three times a day, November 27 through November 29, 2026. No photos.", ["Max", "Bruno"], "2026-11-27", "2026-11-29", 3, "walking", False),
    ("Feed the cats (Mochi and Cleo) twice a day from January 20 to January 23, 2027, photos yes.", ["Mochi", "Cleo"], "2027-01-20", "2027-01-23", 2, "feeding", True),
]
for i, (text, pets, s, e, vpd, svc, photo) in enumerate(multi, 1):
    add(case(f"multi-{i:03d}", "multiple_pets", text, pets, pet=None, start=s, end=e, vpd=vpd, service=svc, photo=photo))

# ------------------------------------------------------------- ambiguous dates (12)
amb = [
    ("I'll be away next weekend. Please feed Luna twice a day and send photos.", None, None),
    ("Feed Luna twice a day from the 6th to the 9th, photos please.", None, None),
    ("Feed Luna twice a day from 6/11 to 9/11, photos please.", None, None),
    ("This Friday through Monday, please feed Luna twice a day. Photos please.", None, None),
    ("Feed Luna twice a day for a few days in December, photos please.", None, None),
    ("Feed Luna twice a day starting November 6, 2026, photos please.", "2026-11-06", None),
    ("Feed Luna twice a day until November 9, 2026, photos please.", None, "2026-11-09"),
    ("Feed Luna twice a day over Thanksgiving week, photos please.", None, None),
    ("Feed Luna twice a day from November 6 to November 9, photos please.", "2026-11-06", "2026-11-09"),
    ("Feed Luna twice a day from January 2 to January 4, photos please.", "2027-01-02", "2027-01-04"),
    ("Feed Luna twice a day sometime after the holidays, photos please.", None, None),
    ("Feed Luna twice a day from 03/04/2027 to 05/04/2027, photos please.", None, None),
]
for i, (text, s, e) in enumerate(amb, 1):
    add(case(f"ambiguous-{i:03d}", "ambiguous_dates", text, ["Luna"], pet="Luna", start=s, end=e, vpd=2,
             service="feeding", photo=True))

# ----------------------------------------------------------- visit times (12)
times_cases = [
    ("Feed Luna twice a day from November 6 to November 9, 2026, photos please.", ()),
    ("Feed Luna in the morning and evening from November 6 to November 9, 2026, photos please.", ()),
    ("Walk Max twice daily, once early and once late, Dec 1-3 2026, no photos.", ()),
    ("Check in on Milo once a day sometime in the afternoon, Nov 12-14 2026, photos yes.", ()),
    ("Feed Luna twice a day around breakfast and dinner, November 6 to November 9, 2026, photos please.", ()),
    ("Walk Max twice a day whenever it suits you, December 1 to December 3, 2026, no photos.", ()),
    ("Feed Luna twice a day at 8am and 6pm from November 6 to November 9, 2026, photos please.", (("08:00", None), ("18:00", None))),
    ("Walk Max once a day between 7 and 9am, December 1 to December 3, 2026, no photos.", (("07:00", "09:00"),)),
    ("Check in on Milo daily at 12:30 from November 12 to November 14, 2026, photos yes.", (("12:30", None),)),
    ("Feed Luna twice a day, between 7 and 8am and between 5 and 6:30pm, November 6-9 2026, photos please.", (("07:00", "08:00"), ("17:00", "18:30"))),
    ("Walk Max twice a day at 7:15 and 19:00 from December 1 to December 3, 2026. No photos.", (("07:15", None), ("19:00", None))),
    ("Check in on Milo once a day at 3pm, November 12 to 14, 2026, photos yes.", (("15:00", None),)),
]
for i, (text, times) in enumerate(times_cases, 1):
    pet = "Luna" if "Luna" in text else "Max" if "Max" in text else "Milo"
    s, e, vpd, svc, photo = {"Luna": ("2026-11-06", "2026-11-09", 2, "feeding", True),
                             "Max": ("2026-12-01", "2026-12-03", 2 if "twice" in text else 1, "walking", False),
                             "Milo": ("2026-11-12", "2026-11-14", 1, "check_in", True)}[pet]
    add(case(f"times-{i:03d}", "visit_times", text, [pet], pet=pet, start=s, end=e, vpd=vpd, service=svc,
             photo=photo, times=times))

# --------------------------------------------------- special feeding instructions (10)
special = [
    ("Feed Luna twice a day Nov 6-9 2026. She gets half a cup of dry food and fresh water each visit. Photos please.", ["half a cup", "dry food"]),
    ("Feed Max twice a day Dec 1-3 2026; grain-free kibble only, no table scraps. No photos.", ["grain-free", "table scraps"]),
    ("Please feed Milo once a day Nov 12-14 2026. Wet food is in the fridge, warm it for 10 seconds. Photos yes.", ["wet food", "fridge"]),
    ("Feed Bella twice a day from November 20 to 23, 2026. Mix the joint supplement into breakfast. No photos.", ["joint supplement", "breakfast"]),
    ("Feed Oliver twice a day Dec 5-7 2026, use the slow feeder bowl because he eats too fast. Photos please.", ["slow feeder"]),
    ("Feed Daisy twice a day from January 5 to 8, 2027. Only the prescription diet food in the blue bag. No photos.", ["prescription diet", "blue bag"]),
    ("Feed Coco once a day Nov 15-18 2026, one can of salmon pate, and refill the water fountain. Photos please.", ["salmon", "water fountain"]),
    ("Feed Leo twice a day, Nov 25-28 2026. Keep the balcony door closed and feed him in the kitchen. No photos.", ["balcony door", "kitchen"]),
    ("Feed Nala twice a day Feb 14-16 2027; she is shy, put the bowl down and leave the room. Photos please.", ["shy", "leave the room"]),
    ("Feed Finn once a day March 3-6 2027, 1/3 cup of kibble, no treats please. No photos.", ["1/3 cup", "no treats"]),
]
spec_meta = {"Luna": ("2026-11-06", "2026-11-09"), "Max": ("2026-12-01", "2026-12-03"), "Milo": ("2026-11-12", "2026-11-14"),
             "Bella": ("2026-11-20", "2026-11-23"), "Oliver": ("2026-12-05", "2026-12-07"), "Daisy": ("2027-01-05", "2027-01-08"),
             "Coco": ("2026-11-15", "2026-11-18"), "Leo": ("2026-11-25", "2026-11-28"), "Nala": ("2027-02-14", "2027-02-16"),
             "Finn": ("2027-03-03", "2027-03-06")}
for i, (text, kw) in enumerate(special, 1):
    pet = next(name for name in spec_meta if name in text)
    s, e = spec_meta[pet]
    vpd = 1 if "once a day" in text else 2
    photo = not ("No photos" in text)
    add(case(f"special-{i:03d}", "special_instructions", text, [pet], pet=pet, start=s, end=e, vpd=vpd,
             service="feeding", photo=photo, keywords=kw))

# ------------------------------------------------------- photo update requests (10)
photo_cases = [
    ("Feed Luna twice a day Nov 6-9 2026. Please text me a picture after each visit.", True),
    ("Feed Luna twice a day Nov 6-9 2026. Pictures aren't necessary.", False),
    ("Feed Luna twice a day Nov 6-9 2026.", None),
    ("Feed Luna twice a day Nov 6-9 2026 and send a quick photo so I know she's okay.", True),
    ("Feed Luna twice a day Nov 6-9 2026. Please don't send photos, my phone storage is full.", False),
    ("Feed Luna twice a day Nov 6-9 2026, photo updates would be lovely.", True),
    ("Feed Luna twice a day Nov 6-9 2026. Skip the photos.", False),
    ("Feed Luna twice a day Nov 6-9 2026. A daily video or photo would be nice.", True),
    ("Feed Luna twice a day Nov 6-9 2026. No need for updates of any kind.", False),
    ("Feed Luna twice a day Nov 6-9 2026, and water the plants.", None),
]
for i, (text, photo) in enumerate(photo_cases, 1):
    add(case(f"photo-{i:03d}", "photo_requests", text, ["Luna"], pet="Luna", start="2026-11-06", end="2026-11-09",
             vpd=2, service="feeding", photo=photo, keywords=["plants"] if "plants" in text else ()))

# ---------------------------------------------------- contradictory instructions (8)
contra = [
    ("Feed Luna twice a day Nov 6-9 2026, actually just once a day is fine, wait no, twice. Hmm, once. Photos please.",
     dict(vpd=None, photo=True)),
    ("Feed Luna twice a day Nov 6-9 2026. Send photos. Actually no photos please. Or maybe yes.", dict(vpd=2, photo=None)),
    ("Feed Luna twice a day from November 6 to November 9, 2026 - sorry, I mean November 7 to 10. Or the 6th to 9th? Photos please.",
     dict(vpd=2, photo=True, start=None, end=None)),
    ("Walk Max once a day Dec 1-3 2026. He is a cat so no walks needed, just feeding. No photos.", dict(vpd=1, photo=False, service=None, pet="Max")),
    ("Feed Luna three times a day Nov 6-9 2026, that's twice a day. Photos please.", dict(vpd=None, photo=True)),
    ("Feed Luna twice a day Nov 6-9 2026, no photos, and send a photo after each visit.", dict(vpd=2, photo=None)),
    ("Feed Luna twice a day Nov 6-9 2026, photos please. Please only visit once a day.", dict(vpd=None, photo=True)),
    ("I need walking for Luna, no wait, feeding only, or walking - you decide. Twice a day Nov 6-9 2026, photos please.",
     dict(vpd=2, photo=True, service=None)),
]
for i, (text, over) in enumerate(contra, 1):
    pet = over.pop("pet", "Luna")
    base = dict(pet=pet, start="2026-11-06" if pet == "Luna" else "2026-12-01",
                end="2026-11-09" if pet == "Luna" else "2026-12-03",
                service="walking" if pet == "Max" else "feeding")
    base.update(over)
    add(case(f"contradictory-{i:03d}", "contradictory", text, [pet], **base))

# ------------------------------------------------------- unsupported services (8)
unsupported = [
    ("Please groom Luna and trim her nails while I'm away Nov 6-9 2026, once a day, photos please.", 1, True),
    ("Take Max to the vet on December 2, 2026 and back. Once. No photos.", 1, False),
    ("Can you give Luna a bath twice a day from November 6 to November 9, 2026? Photos please.", 2, True),
    ("Please train Max to sit, once a day Dec 1-3 2026, no photos.", 1, False),
    ("I need overnight boarding for Luna from November 6 to November 9, 2026. Photos please.", None, True),
    ("Please drive Max to doggy daycare every morning Dec 1-3 2026, once a day. No photos.", 1, False),
    ("Can someone administer Luna's insulin injections twice a day Nov 6-9 2026? Photos please.", 2, True),
    ("Please clean the whole apartment once a day Nov 6-9 2026 while Luna is home. Photos please.", 1, True),
]
for i, (text, vpd, photo) in enumerate(unsupported, 1):
    pet = "Luna" if "Luna" in text else "Max"
    s, e = ("2026-11-06", "2026-11-09") if pet == "Luna" else ("2026-12-01", "2026-12-03")
    if "vet on December 2" in text:
        s = e = "2026-12-02"
    add(case(f"unsupported-{i:03d}", "unsupported_services", text, [pet], pet=pet, start=s, end=e, vpd=vpd,
             service=None, photo=photo))

# ------------------------------------------------------ invalid date ranges (9)
invalid = [
    ("Care from November 9 to November 6, 2026, feeding once a day for Luna, photos yes.", "2026-11-09", "2026-11-06", 1),
    ("Feed Luna twice a day from December 20, 2026 to December 18, 2026, no photos.", "2026-12-20", "2026-12-18", 2),
    ("Walk Luna once a day from January 10, 2027 to January 5, 2027, photos please.", "2027-01-10", "2027-01-05", 1),
    ("Feed Luna twice a day from 2026-11-30 to 2026-11-01, photos please.", "2026-11-30", "2026-11-01", 2),
    ("Check in on Luna once a day from March 5, 2027 to February 28, 2027. No photos.", "2027-03-05", "2027-02-28", 1),
    ("Feed Luna twice daily, start December 31, 2026, end December 1, 2026, photos yes.", "2026-12-31", "2026-12-01", 2),
    ("From November 15, 2026 back to November 12, 2026, feed Luna once a day. Photos please.", "2026-11-15", "2026-11-12", 1),
    ("Feed Luna twice a day from April 2, 2027 until March 30, 2027, no photos.", "2027-04-02", "2027-03-30", 2),
    ("Full care for Luna once a day from February 20, 2027 to February 10, 2027, photos please.", "2027-02-20", "2027-02-10", 1),
]
for i, (text, s, e, vpd) in enumerate(invalid, 1):
    svc = "walking" if "Walk" in text else "check_in" if "Check in" in text else "full_care" if "Full care" in text else "feeding"
    photo = not ("no photos" in text.lower())
    add(case(f"invalid-{i:03d}", "invalid_date_range", text, ["Luna"], pet="Luna", start=s, end=e, vpd=vpd,
             service=svc, photo=photo, invalid_range=True))


def main() -> None:
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "duplicate ids"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for c in CASES:
            fh.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"Wrote {len(CASES)} cases to {OUT}")


if __name__ == "__main__":
    main()
