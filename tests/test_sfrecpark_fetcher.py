from __future__ import annotations
from datetime import date, datetime
from pathlib import Path

from sfevents.fetchers.sfrecpark import SFRecParkFetcher, _month_window

FIXTURE = Path(__file__).parent / "fixtures" / "sfrecpark_sample.html"


def parsed():
    return SFRecParkFetcher().parse(FIXTURE.read_text())


def test_parses_every_event_block_in_the_fixture():
    assert len(parsed()) == 3


def test_reads_title_from_event_microdata_not_the_visible_heading():
    titles = [e.title for e in parsed()]
    assert "Golden Gate Bandshell: Friday Happy Hour" in titles


def test_start_times_keep_their_time_of_day():
    """The point of this source: unlike DoTheBay, these aren't all midnight."""
    events = parsed()
    assert all(isinstance(e.start, datetime) for e in events)
    assert any(e.start.hour != 0 for e in events)
    first = next(e for e in events if "Friday Happy Hour" in e.title)
    assert first.start == datetime(2026, 9, 18, 16, 30)


def test_nested_location_name_becomes_the_venue_not_the_title():
    """An Event has a `name` and so does its location Place - they must not collide."""
    event = next(e for e in parsed() if "Friday Happy Hour" in e.title)
    assert event.venue == "Golden Gate Bandshell"
    assert event.title != event.venue


def test_postal_address_parts_are_joined():
    event = next(e for e in parsed() if "Friday Happy Hour" in e.title)
    assert "Music Concourse Drive" in event.address
    assert "San Francisco" in event.address
    assert "94118" in event.address


def test_source_id_comes_from_the_eid_so_it_is_stable():
    ids = [e.source_id for e in parsed()]
    assert all(i.isdigit() for i in ids), ids
    assert len(set(ids)) == len(ids)


def test_url_points_at_the_event_detail_page():
    event = parsed()[0]
    assert event.url.startswith("https://sfrecpark.org/Calendar.aspx?EID=")


def test_cost_is_left_empty_rather_than_guessed():
    """Most listings are free but some are ticketed, and the page doesn't say."""
    assert all(e.cost == "" for e in parsed())
    assert all(e.is_free is False for e in parsed())


def test_every_event_is_tagged_so_category_filters_reach_this_source():
    assert all(e.categories == ["Parks & Rec"] for e in parsed())


def test_description_is_captured():
    event = next(e for e in parsed() if "Friday Happy Hour" in e.title)
    assert "Golden Gate Bandshell" in event.description


def test_garbage_html_yields_no_events_rather_than_raising():
    assert SFRecParkFetcher().parse("<html><body>nothing here</body></html>") == []


def test_event_without_a_start_date_is_skipped():
    html = """<div itemscope itemtype="http://schema.org/Event">
      <span itemprop="name">Undated thing</span></div>"""
    assert SFRecParkFetcher().parse(html) == []


def test_month_window_walks_forward_across_the_year_boundary():
    assert list(_month_window(date(2026, 11, 15), 4)) == [
        (2026, 11), (2026, 12), (2027, 1), (2027, 2),
    ]


def test_facility_hours_and_meetings_are_not_events():
    from sfevents.fetchers.sfrecpark import is_event
    assert not is_event("Cycle Track Open After 6:45 PM")
    assert not is_event("Cycle Track Closed All Day Due to Special Event")
    assert not is_event("Joint Zoo Committee")
    assert not is_event("Recreation and Park Commission Meeting")
    assert not is_event("PROSAC")
    assert is_event("Free Dance Fitness Class by Rae Studios")
    assert is_event("Sunset Dunes Community Cleanup")
