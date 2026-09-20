from __future__ import annotations
from pathlib import Path

from sfevents.fetchers.dothebay import DoTheBayFetcher

FIXTURE = Path(__file__).parent / "fixtures" / "dothebay_sample.html"


def load_events():
    return DoTheBayFetcher().parse(FIXTURE.read_text())


def test_parses_all_event_cards():
    events = load_events()
    assert len(events) == 4


def test_extracts_title_date_and_url_from_event_link():
    events = load_events()
    flowers = next(e for e in events if "Brandon Flowers" in e.title)
    assert flowers.start.year == 2026 and flowers.start.month == 9 and flowers.start.day == 5
    assert flowers.url == "https://dothebay.com/events/2026/9/5/brandon-flowers-tickets"
    assert flowers.source == "dothebay"
    assert flowers.source_id == flowers.url


def test_extracts_venue_from_venue_link():
    events = load_events()
    flowers = next(e for e in events if "Brandon Flowers" in e.title)
    assert flowers.venue == "Fox Theater"


def test_extracts_address_from_maps_link():
    events = load_events()
    flowers = next(e for e in events if "Brandon Flowers" in e.title)
    assert flowers.address == "1807 Telegraph Ave, Oakland, CA, 94612"


def test_event_without_a_venue_link_has_empty_venue():
    """Real DoTheBay pages sometimes show the venue as plain text, not a link."""
    events = load_events()
    cowboy = next(e for e in events if "Stardust Cowboy" in e.title)
    assert cowboy.venue == ""
    assert cowboy.address == ""


def test_start_time_is_always_midnight_not_fabricated():
    """DoTheBay doesn't expose time-of-day in a parseable way - documented limitation."""
    events = load_events()
    for e in events:
        assert e.start.hour == 0 and e.start.minute == 0


def test_cost_and_categories_are_not_fabricated():
    events = load_events()
    for e in events:
        assert e.cost == ""
        assert e.categories == []


def test_footer_links_are_not_mistaken_for_venues():
    """/venues (no slug) and /artists must not match the venue-link pattern."""
    events = load_events()
    assert all(e.venue not in ("The Bay Area Venues", "Artists in The Bay Area") for e in events)


def test_empty_html_returns_no_events():
    assert DoTheBayFetcher().parse("<html><body>nothing here</body></html>") == []


def test_event_link_with_no_text_is_ignored():
    html = '<html><body><a href="/events/2026/9/5/no-title-tickets"></a></body></html>'
    assert DoTheBayFetcher().parse(html) == []


def test_malformed_event_href_is_ignored():
    """Single-digit-less or otherwise malformed event URLs shouldn't match."""
    html = '<html><body><a href="/events/26/9/5/bad-year-tickets">Bad Year</a></body></html>'
    assert DoTheBayFetcher().parse(html) == []


def test_fetch_uses_urlopen_with_correct_url(monkeypatch):
    # days_ahead=0 keeps the original single-page behaviour; the default
    # now walks one page per day (see test_fetch_walks_one_page_per_day).
    fetcher = DoTheBayFetcher(path="/events/today", days_ahead=0)
    fixture_html = FIXTURE.read_text()
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return fixture_html.encode("utf-8")

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(
        "sfevents.fetchers.dothebay.urllib.request.urlopen", fake_urlopen
    )
    events = fetcher.fetch()
    assert len(events) == 4
    assert captured["url"] == "https://dothebay.com/events/today"
    assert captured["timeout"] == fetcher.timeout


CARDS_FIXTURE = Path(__file__).parent / "fixtures" / "dothebay_cards.html"


def test_cover_images_are_attached_to_the_right_events():
    """Every card carries its own artwork - sharing one image would be a bug."""
    events = DoTheBayFetcher().parse(CARDS_FIXTURE.read_text())
    with_images = [e for e in events if e.images]
    assert len(with_images) == 3
    urls = [e.images[0] for e in with_images]
    assert len(set(urls)) == len(urls), "events must not share one image"
    assert all(u.startswith("https://") for u in urls)


def test_a_card_without_its_own_image_is_not_given_the_next_cards():
    """The pairing must not run past a card boundary."""
    html = (
        '<div data-permalink="/events/2026/9/20/first-event"></div>'
        '<a href="/events/2026/9/20/first-event">First Event</a>'
        '<div data-permalink="/events/2026/9/20/second-event">'
        "<div style=\"background-image:url('https://img/second.jpg');\"></div></div>"
        '<a href="/events/2026/9/20/second-event">Second Event</a>'
    )
    events = {e.title: e for e in DoTheBayFetcher().parse(html)}
    assert events["First Event"].images == []
    assert events["Second Event"].images == ["https://img/second.jpg"]


def test_listing_without_images_still_parses_events():
    """The original anchor-only fixture has no cards; events must survive."""
    events = DoTheBayFetcher().parse(FIXTURE.read_text())
    assert len(events) == 4
    assert all(e.images == [] for e in events)


def test_fetch_walks_one_page_per_day():
    from datetime import date

    fetcher = DoTheBayFetcher(days_ahead=3, today=date(2026, 9, 20))
    assert fetcher._paths() == [
        "/events/2026/9/20", "/events/2026/9/21", "/events/2026/9/22",
    ]


def test_days_ahead_zero_falls_back_to_the_single_today_page():
    fetcher = DoTheBayFetcher(days_ahead=0)
    assert fetcher._paths() == ["/events/today"]


def test_fetch_deduplicates_events_appearing_on_several_days():
    """Multi-day events are listed on each of their days."""
    from datetime import date
    import urllib.request

    page = CARDS_FIXTURE.read_text()
    calls = []

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return page.encode()

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return FakeResponse()

    original = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        events = DoTheBayFetcher(days_ahead=4, today=date(2026, 9, 20)).fetch()
    finally:
        urllib.request.urlopen = original

    assert len(calls) == 4, "should request one page per day"
    assert len(events) == 3, "the same 3 events must not be repeated 4 times"
