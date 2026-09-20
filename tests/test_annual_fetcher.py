from __future__ import annotations
from datetime import date

import pytest

from sfevents.fetchers.annual import (
    AnnualEventsFetcher, _nth_weekday, _resolve_annual, _add_months,
)

TODAY = date(2026, 9, 18)


def test_nth_weekday_counts_from_the_start_of_the_month():
    # September 2026: the 1st is a Tuesday, so Sundays fall on 6/13/20/27.
    assert _nth_weekday(2026, 9, 6, 1) == date(2026, 9, 6)
    assert _nth_weekday(2026, 9, 6, 3) == date(2026, 9, 20)


def test_nth_weekday_minus_one_is_the_last_one():
    assert _nth_weekday(2026, 9, 6, -1) == date(2026, 9, 27)


def test_nth_weekday_returns_none_when_there_is_no_fifth():
    assert _nth_weekday(2026, 9, 6, 5) is None


def test_folsom_street_fair_rule_is_the_last_sunday_of_september():
    """The rule this list is built on: a real civic date, not an approximation."""
    rule = {"type": "nth_weekday", "month": 9, "weekday": 6, "n": -1}
    assert _resolve_annual(rule, 2026) == date(2026, 9, 27)
    assert _resolve_annual(rule, 2027) == date(2027, 9, 26)
    assert _resolve_annual(rule, 2028) == date(2028, 9, 24)
    assert all(_resolve_annual(rule, y).weekday() == 6 for y in range(2026, 2036))


def test_bay_to_breakers_rule_is_the_third_sunday_of_may():
    rule = {"type": "nth_weekday", "month": 5, "weekday": 6, "n": 3}
    for year in range(2026, 2036):
        d = _resolve_annual(rule, year)
        assert d.weekday() == 6 and 15 <= d.day <= 21


def test_last_full_weekend_keeps_the_sunday_inside_the_month():
    """SF Pride is the last FULL weekend of June - a Sat/Sun that doesn't straddle July."""
    rule = {"type": "last_full_weekend", "month": 6, "span_days": 2}
    for year in range(2026, 2040):
        saturday = _resolve_annual(rule, year)
        assert saturday.weekday() == 5
        assert saturday.month == 6
        assert (saturday.replace(day=saturday.day + 1)).month == 6


def test_after_nth_weekday_gives_the_friday_after_thanksgiving():
    # US Thanksgiving is the 4th Thursday of November; the tree lighting is
    # the next day.
    rule = {"type": "after_nth_weekday", "month": 11, "weekday": 3, "n": 4, "offset_days": 1}
    assert _resolve_annual(rule, 2026) == date(2026, 11, 27)
    assert _resolve_annual(rule, 2027) == date(2027, 11, 26)
    assert _resolve_annual(rule, 2026).weekday() == 4


def test_unknown_rule_type_is_an_error_not_a_silent_skip():
    with pytest.raises(ValueError, match="unknown rule type"):
        _resolve_annual({"type": "whenever", "month": 1}, 2026)


def test_add_months_clamps_to_the_shorter_month():
    assert _add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert _add_months(date(2026, 12, 15), 12) == date(2027, 12, 15)


def test_monthly_rule_fires_every_month_in_the_horizon():
    entries = [{
        "id": "first-friday", "title": "First Friday", "notability": 10,
        "rule": {"type": "monthly_nth_weekday", "weekday": 4, "n": 1},
    }]
    events = AnnualEventsFetcher(horizon_months=6, today=TODAY).build(entries)
    assert len(events) == 6
    assert all(e.start.weekday() == 4 and e.start.day <= 7 for e in events)


def test_occurrences_stay_inside_the_horizon():
    fetcher = AnnualEventsFetcher(horizon_months=3, today=TODAY)
    events = fetcher.fetch()
    assert events
    for e in events:
        assert TODAY <= e.start.date() <= date(2026, 12, 18)


def test_multi_day_events_get_an_end_date():
    entries = [{
        "id": "three-day", "title": "Three Day Fest", "notability": 50,
        "rule": {"type": "nth_weekday", "month": 10, "weekday": 4, "n": 1, "span_days": 3},
    }]
    [event] = AnnualEventsFetcher(horizon_months=3, today=TODAY).build(entries)
    assert event.start.date() == date(2026, 10, 2)
    assert event.end.date() == date(2026, 10, 4)


def test_single_day_events_have_no_end_date():
    entries = [{
        "id": "one-day", "title": "One Day", "notability": 50,
        "rule": {"type": "fixed", "month": 10, "day": 31},
    }]
    [event] = AnnualEventsFetcher(horizon_months=3, today=TODAY).build(entries)
    assert event.end is None


def test_source_id_is_stable_per_occurrence_so_refetching_is_idempotent():
    fetcher = AnnualEventsFetcher(today=TODAY)
    first = {e.source_id for e in fetcher.fetch()}
    second = {e.source_id for e in fetcher.fetch()}
    assert first == second
    assert all(":" in sid for sid in first)


def test_approx_entries_propagate_the_flag_to_every_occurrence():
    entries = [
        {"id": "a", "title": "Guessy", "approx": True,
         "rule": {"type": "fixed", "month": 10, "day": 1}},
        {"id": "b", "title": "Certain", "approx": False,
         "rule": {"type": "fixed", "month": 10, "day": 2}},
    ]
    events = {e.title: e for e in AnnualEventsFetcher(horizon_months=3, today=TODAY).build(entries)}
    assert events["Guessy"].date_approx is True
    assert events["Certain"].date_approx is False


def test_shipped_data_file_is_well_formed():
    """Guards the curated list itself: every entry must be resolvable."""
    fetcher = AnnualEventsFetcher(today=TODAY)
    import json
    spec = json.loads(fetcher.data_file.read_text())
    entries = spec["events"]
    assert len(entries) >= 40
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "duplicate ids"
    for e in entries:
        assert e["title"] and e["rule"]["type"]
        assert 0 <= e.get("notability", 0) <= 100
    # And it actually materializes.
    events = fetcher.fetch()
    assert len(events) >= 40


def test_mill_valley_fall_arts_is_present_and_dated():
    """Explicitly requested coverage - the scraped sources never surface it."""
    events = AnnualEventsFetcher(today=TODAY).fetch()
    matches = [e for e in events if "Mill Valley Fall Arts" in e.title]
    assert matches, "Mill Valley Fall Arts Festival missing from the annual list"
    event = matches[0]
    assert event.start.month == 9
    assert 15 <= event.start.day <= 21, "should land on the third weekend"
    assert event.date_approx is True  # timing is typical, not announced
    assert event.venue and event.url
