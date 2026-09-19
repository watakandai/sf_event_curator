from __future__ import annotations
from pathlib import Path

from sf_event_curator.fetchers.dothebay import DoTheBayFetcher

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
    fetcher = DoTheBayFetcher(path="/events/today")
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
        "sf_event_curator.fetchers.dothebay.urllib.request.urlopen", fake_urlopen
    )
    events = fetcher.fetch()
    assert len(events) == 4
    assert captured["url"] == "https://dothebay.com/events/today"
    assert captured["timeout"] == fetcher.timeout
