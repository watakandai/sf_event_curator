from __future__ import annotations
import json
from datetime import date, datetime

from sf_event_curator.fetchers.seasonal import DATA_FILE, SeasonalFetcher, windows

TODAY = date(2026, 9, 19)


def entry(**overrides):
    base = {"id": "whales", "title": "Gray whales", "from": [12, 15], "to": [4, 30]}
    return {**base, **overrides}


def test_a_window_that_wraps_the_new_year_ends_the_next_spring():
    assert windows([12, 15], [4, 30], TODAY, date(2027, 9, 19)) == [
        (date(2026, 12, 15), date(2027, 4, 30)),
    ]


def test_a_season_already_under_way_keeps_its_real_start():
    # Whale season opened in April; in September it's still on.
    got = windows([4, 1], [11, 30], TODAY, date(2027, 9, 19))
    assert got[0] == (date(2026, 4, 1), date(2026, 11, 30))
    assert got[1] == (date(2027, 4, 1), date(2027, 11, 30))


def test_a_season_that_ended_this_year_is_not_emitted():
    assert windows([3, 1], [4, 30], TODAY, date(2027, 1, 1)) == []


def test_seasons_are_tagged_seasonal_and_approximate_by_default():
    events = SeasonalFetcher(today=TODAY).build([entry(categories=["Outdoors"])])
    assert len(events) == 1
    e = events[0]
    assert e.source == "seasonal_bay_area"
    assert e.source_id == "whales:2026-12-15"
    assert (e.start, e.end) == (datetime(2026, 12, 15), datetime(2027, 4, 30))
    assert e.categories == ["Seasonal", "Outdoors"]
    assert e.date_approx


def test_a_fixed_legal_season_can_opt_out_of_approx():
    events = SeasonalFetcher(today=TODAY).build([entry(approx=False)])
    assert not events[0].date_approx


def test_the_shipped_list_is_valid():
    spec = json.loads(DATA_FILE.read_text())
    ids = [s["id"] for s in spec["seasons"]]
    assert len(ids) == len(set(ids))
    for s in spec["seasons"]:
        date(2027, *s["from"]), date(2027, *s["to"])  # real month/day pairs
        assert s["title"] and s["description"]
    assert SeasonalFetcher(today=TODAY).fetch()
