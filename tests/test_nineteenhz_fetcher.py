from __future__ import annotations
from datetime import datetime
from pathlib import Path

from sfevents.fetchers.nineteenhz import (
    NineteenHzFetcher, _first_time, _price, _split_title_venue,
)

FIXTURE = Path(__file__).parent / "fixtures" / "nineteenhz_sample.html"


def parsed():
    return NineteenHzFetcher().parse(FIXTURE.read_text())


def test_parses_data_rows_and_skips_the_header():
    events = parsed()
    assert len(events) == 4
    assert all(e.title for e in events)


def test_unclosed_title_cells_do_not_swallow_the_next_column():
    """The live page leaves <td> unclosed after the title - see the fixture comment."""
    event = next(e for e in parsed() if "Destination Festival" in e.title)
    assert event.title == "Destination Festival 2026"
    assert event.venue == "Forbes Island (San Francisco)"
    # The genre column must survive as categories, not be glued into the venue.
    assert "house" in event.categories


def test_date_comes_from_the_hidden_sort_column():
    events = parsed()
    assert all(e.start.date() == datetime(2026, 9, 18).date() for e in events)


def test_time_of_day_is_parsed_from_the_display_column():
    event = next(e for e in parsed() if "Destination Festival" in e.title)
    assert event.start == datetime(2026, 9, 18, 14, 0)


def test_first_time_handles_am_pm_and_minutes():
    assert _first_time("(5pm-10pm)") == (17, 0)
    assert _first_time("(9:30pm-2am)") == (21, 30)
    assert _first_time("(11am-4pm)") == (11, 0)
    assert _first_time("(12am-6am)") == (0, 0)
    assert _first_time("(12pm-5pm)") == (12, 0)
    assert _first_time("no time here") == (0, 0)


def test_age_restrictions_are_not_recorded_as_prices():
    """"21+" in the price column means no listed price, not twenty-one dollars."""
    assert _price("21+") == ""
    assert _price("All ages") == ""
    assert _price("$15 | all ages") == "$15"
    assert _price("$250-300+ | All ages") == "$250-300+"
    assert _price("") == ""


def test_free_events_are_normalized_to_the_is_free_convention():
    events = NineteenHzFetcher().parse(
        FIXTURE.read_text().replace("$250-300+ | All ages", "Free | 21+")
    )
    freebie = next(e for e in events if "Destination Festival" in e.title)
    assert freebie.cost == "0"
    assert freebie.is_free is True


def test_donation_based_entry_counts_as_a_price_not_a_blank():
    assert _price("donation | 21+") == "donation"


def test_split_title_venue_without_an_at_sign_leaves_venue_empty():
    assert _split_title_venue("Just A Title") == ("Just A Title", "")


def test_categories_are_capped_so_one_row_cannot_flood_the_filter_list():
    tags = "a, b, c, d, e, f, g, h"
    events = NineteenHzFetcher().parse(FIXTURE.read_text().replace("house, bands", tags))
    event = next(e for e in events if "Destination Festival" in e.title)
    assert len(event.categories) == 6


def test_source_ids_are_unique_and_prefer_the_event_link():
    events = parsed()
    ids = [e.source_id for e in events]
    assert len(set(ids)) == len(ids)
    linked = next(e for e in events if "Destination Festival" in e.title)
    assert linked.source_id.startswith("https://")


def test_garbage_html_yields_no_events_rather_than_raising():
    assert NineteenHzFetcher().parse("<html><body>no table</body></html>") == []


def test_row_without_a_sortable_date_is_skipped():
    html = "<table><tr><td>Fri: Sep 18</td><td>Thing</td><td></td><td></td><td></td><td></td><td></td></tr></table>"
    assert NineteenHzFetcher().parse(html) == []
