from __future__ import annotations
import json
from datetime import date, datetime, timedelta

from sfevents import plans as club
from sfevents.db import init_db, upsert_events
from sfevents.models import Event

TODAY = date(2026, 9, 19)


def _event(**kw):
    e = {
        "id": 1, "source": "funcheap_sf", "source_id": "abc", "title": "Pumpkin Festival",
        "start_ts": "2026-10-17T00:00:00", "end_ts": "2026-10-18T00:00:00",
        "venue": "Main St", "address": "Half Moon Bay", "url": "https://example.com/p",
    }
    e.update(kw)
    e["key"] = club.event_key(e)
    return e


def test_event_key_is_source_and_source_id():
    assert club.event_key({"source": "dothebay", "source_id": "42"}) == "dothebay:42"


def test_load_plans_missing_file_is_empty_club(tmp_path):
    config = club.load_plans(tmp_path / "nope.json")
    assert config["plans"] == [] and config["club"] == "Saturday Adventure Club"


def test_resolve_uses_the_events_details():
    plans, warnings = club.resolve_plans(
        {"plans": [{"event": "funcheap_sf:abc", "note": "carpool from the Mission"}]},
        [_event()], TODAY,
    )
    assert warnings == []
    [p] = plans
    assert p["title"] == "Pumpkin Festival" and p["event_id"] == 1
    assert p["all_day"] is True and p["start"].startswith("2026-10-17")
    assert p["note"] == "carpool from the Mission"


def test_resolve_pins_a_day_and_time_within_a_season():
    season = _event(source="seasonal_bay_area", source_id="whales:2026-12-15",
                    start_ts="2026-12-15T00:00:00", end_ts="2027-04-30T00:00:00")
    [p], _ = club.resolve_plans(
        {"plans": [{"event": season["key"], "date": "2027-01-09", "time": "09:30"}]},
        [season], TODAY,
    )
    assert p["start"] == "2027-01-09T09:30:00" and p["all_day"] is False
    assert p["end"] == "2027-01-09T11:30:00"


def test_resolve_converts_aware_times_to_local_wall_clock():
    [p], _ = club.resolve_plans(
        {"plans": [{"event": "funcheap_sf:abc"}]},
        [_event(start_ts="2026-10-17T17:00:00+00:00", end_ts=None)], TODAY,
    )
    assert p["start"] == "2026-10-17T10:00:00" and p["all_day"] is False


def test_resolve_warns_on_unknown_key_and_keeps_standalone_plans():
    plans, warnings = club.resolve_plans(
        {"plans": [
            {"event": "funcheap_sf:gone"},
            {"title": "Bonfire at Ocean Beach", "date": "2026-10-03", "time": "18:00"},
        ]},
        [_event()], TODAY,
    )
    assert len(warnings) == 1 and "funcheap_sf:gone" in warnings[0]
    assert [p["title"] for p in plans] == ["Bonfire at Ocean Beach"]


def test_resolve_drops_past_plans_and_sorts():
    plans, _ = club.resolve_plans(
        {"plans": [
            {"title": "Later", "date": "2026-11-01"},
            {"title": "Past", "date": "2026-09-01"},
            {"title": "Sooner", "date": "2026-09-26"},
        ]},
        [], TODAY,
    )
    assert [p["title"] for p in plans] == ["Sooner", "Later"]


def test_ics_feed():
    plans, _ = club.resolve_plans(
        {"plans": [
            {"event": "funcheap_sf:abc"},
            {"title": "Whales, Point Reyes; bring layers", "date": "2027-01-09", "time": "09:30"},
        ]},
        [_event()], TODAY,
    )
    ics = club.to_ics({"club": "Saturday Adventure Club", "host": "Kandai"}, plans,
                      "https://x.github.io/y/", now=datetime(2026, 9, 19, 12))
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert "X-WR-CALNAME:Saturday Adventure Club" in ics
    # A Sat-Sun festival covers both days; DTEND is the exclusive day after.
    assert "DTSTART;VALUE=DATE:20261017\r\nDTEND;VALUE=DATE:20261019" in ics
    # 9:30 in January is PST, UTC-8.
    assert "DTSTART:20270109T173000Z" in ics
    assert "SUMMARY:Whales\\, Point Reyes\; bring layers" in ics
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    unfolded = ics.replace("\r\n ", "")
    assert "Kandai is going - want to come? Say you're in: https://x.github.io/y/#club" in unfolded


def test_export_writes_plans_and_feed(tmp_path):
    from tests.test_integration import run_cli

    db_path = tmp_path / "events.db"
    init_db(db_path)
    day = datetime.combine(date.today() + timedelta(days=10), datetime.min.time())
    upsert_events(db_path, [Event("funcheap_sf", "abc", "Pumpkin Festival", day, day)])
    plans_file = tmp_path / "plans.json"
    plans_file.write_text(json.dumps({"host": "Kandai", "contact": "sms:+15550100",
                                      "plans": [{"event": "funcheap_sf:abc"}]}))
    out = tmp_path / "data" / "events.json"
    ics = tmp_path / "club.ics"
    run_cli(["--db", str(db_path), "export", "--out", str(out),
             "--plans", str(plans_file), "--ics-out", str(ics)])

    [event] = json.loads(out.read_text())
    assert event["key"] == "funcheap_sf:abc"
    page = json.loads((tmp_path / "data" / "plans.json").read_text())
    assert page["host"] == "Kandai" and page["contact"] == "sms:+15550100"
    assert [p["title"] for p in page["plans"]] == ["Pumpkin Festival"]
    assert b"\r\nSUMMARY:Pumpkin Festival\r\n" in ics.read_bytes()
