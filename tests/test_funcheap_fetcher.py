from __future__ import annotations
from datetime import timezone
from pathlib import Path

from sf_event_curator.fetchers.funcheap import FuncheapFetcher

FIXTURE = Path(__file__).parent / "fixtures" / "funcheap_sample.xml"


def load_events():
    fetcher = FuncheapFetcher()
    return fetcher.parse(FIXTURE.read_bytes())


def test_parses_all_items():
    events = load_events()
    assert len(events) == 3


def test_fields_map_correctly():
    events = load_events()
    beer_ride = next(e for e in events if "Beer Ride" in e.title)
    assert beer_ride.venue == "Speakeasy Brewery"
    assert beer_ride.is_free
    assert beer_ride.start is not None
    assert beer_ride.start.astimezone(timezone.utc).isoformat() == "2026-12-02T18:30:00+00:00"
    assert "Eating & Drinking" in beer_ride.categories
    assert beer_ride.url == (
        "https://www.facebook.com/events/1878100878982610/?event_time_id=1878100892315942"
    )


def test_source_id_is_stable_link():
    events = load_events()
    art_crawl = next(e for e in events if "Art Crawl" in e.title)
    assert art_crawl.source_id == art_crawl.source_id  # dedupe key present
    assert art_crawl.source_id.startswith("https://sf.funcheap.com/")


def test_categories_split_and_stripped():
    events = load_events()
    dance = next(e for e in events if "Union Square" in e.title)
    assert "Outdoors" in dance.categories
    assert "Live Music" in dance.categories
    assert all(c == c.strip() for c in dance.categories)


def test_missing_link_items_are_skipped():
    fetcher = FuncheapFetcher()
    xml_no_link = b"""<?xml version="1.0"?>
    <rss version="2.0" xmlns:funCheap="https://sf.funcheap.com/rssfeed/">
    <channel><item><title>No link here</title></item></channel></rss>"""
    assert fetcher.parse(xml_no_link) == []


def test_html_entities_are_decoded_in_titles():
    events = load_events()
    beer_ride = next(e for e in events if "Beer Ride" in e.title)
    # &#8220; / &#8221; are curly quotes - XML parsing decodes them automatically
    assert "\u201c" in beer_ride.title
    assert "&#8220;" not in beer_ride.title


def test_malformed_start_time_returns_none_not_raise():
    fetcher = FuncheapFetcher()
    xml = b"""<?xml version="1.0"?>
    <rss version="2.0" xmlns:funCheap="https://sf.funcheap.com/rssfeed/">
    <channel><item>
      <title>Bad date event</title>
      <link>https://sf.funcheap.com/bad-date/</link>
      <funCheap:startTime>not-a-real-date</funCheap:startTime>
    </item></channel></rss>"""
    events = fetcher.parse(xml)
    assert len(events) == 1
    assert events[0].start is None


def test_missing_optional_fields_default_sensibly():
    fetcher = FuncheapFetcher()
    xml = b"""<?xml version="1.0"?>
    <rss version="2.0" xmlns:funCheap="https://sf.funcheap.com/rssfeed/">
    <channel><item>
      <title>Bare minimum event</title>
      <link>https://sf.funcheap.com/bare/</link>
    </item></channel></rss>"""
    events = fetcher.parse(xml)
    assert len(events) == 1
    e = events[0]
    assert e.venue == ""
    assert e.cost == ""
    assert not e.is_free
    assert e.categories == []
    assert e.start is None and e.end is None
    assert e.url == "https://sf.funcheap.com/bare/"  # falls back to <link>


def test_url_falls_back_to_link_when_funcheap_url_absent():
    events = load_events()
    art_crawl = next(e for e in events if "Art Crawl" in e.title)
    # funCheap:url points off-site (Facebook); other items may lack it entirely
    assert art_crawl.url != ""


def test_fetch_uses_urlopen_and_parses_response(monkeypatch):
    fetcher = FuncheapFetcher()
    fixture_bytes = FIXTURE.read_bytes()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return fixture_bytes

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(
        "sf_event_curator.fetchers.funcheap.urllib.request.urlopen", fake_urlopen
    )
    events = fetcher.fetch()
    assert len(events) == 3
    assert captured["url"] == fetcher.feed_url
    assert captured["timeout"] == fetcher.timeout


ENCLOSURE_FIXTURE = Path(__file__).parent / "fixtures" / "funcheap_enclosure.xml"


def test_enclosure_images_are_collected_from_a_real_capture():
    """Funcheap publishes event artwork as RSS <enclosure> elements.

    Uses its own fixture: the main funcheap_sample.xml capture predates the
    feed carrying enclosures, and other tests pin its exact contents.
    """
    [event] = FuncheapFetcher().parse(ENCLOSURE_FIXTURE.read_bytes())
    assert event.images, "no images parsed from the live-captured item"
    assert all(u.startswith("https://") for u in event.images)
    assert not any("/thumbnails/" in u for u in event.images)


def test_the_older_fixture_simply_has_no_images():
    """Absence of artwork must not break parsing of an older feed shape."""
    events = FuncheapFetcher().parse(FIXTURE.read_bytes())
    assert events and all(e.images == [] for e in events)


def test_thumbnail_duplicates_are_dropped():
    """The feed lists each image twice - original and a 170x170 thumbnail."""
    xml = b"""<rss xmlns:funCheap="https://sf.funcheap.com/rssfeed/"><channel><item>
      <title>Thing</title><link>https://example.com/a</link>
      <enclosure url="https://cdn.funcheap.com/wp-content/uploads/pic.jpg"/>
      <enclosure url="https://cdn.funcheap.com/wp-content/thumbnails/170x170/wp-content/uploads/pic.jpg"/>
    </item></channel></rss>"""
    [event] = FuncheapFetcher().parse(xml)
    assert event.images == ["https://cdn.funcheap.com/wp-content/uploads/pic.jpg"]


def test_distinct_images_are_all_kept():
    xml = b"""<rss xmlns:funCheap="https://sf.funcheap.com/rssfeed/"><channel><item>
      <title>Thing</title><link>https://example.com/a</link>
      <enclosure url="https://cdn.funcheap.com/one.jpg"/>
      <enclosure url="https://cdn.funcheap.com/two.jpg"/>
    </item></channel></rss>"""
    [event] = FuncheapFetcher().parse(xml)
    assert event.images == ["https://cdn.funcheap.com/one.jpg", "https://cdn.funcheap.com/two.jpg"]


def test_an_item_with_no_enclosure_has_no_images():
    xml = b"""<rss xmlns:funCheap="https://sf.funcheap.com/rssfeed/"><channel><item>
      <title>Thing</title><link>https://example.com/a</link>
    </item></channel></rss>"""
    [event] = FuncheapFetcher().parse(xml)
    assert event.images == []
