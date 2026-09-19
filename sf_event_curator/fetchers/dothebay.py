from __future__ import annotations
import re
import urllib.request
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin

from ..models import Event

BASE_URL = "https://dothebay.com"
EVENT_HREF_RE = re.compile(r"^/events/(\d{4})/(\d{1,2})/(\d{1,2})/[\w-]+/?$")
VENUE_HREF_RE = re.compile(r"^/venues/[\w-]+/?$")
MAPS_QUERY_RE = re.compile(r"[?&]q=([^&]+)")
# Each listing card pairs a data-permalink (the same path the anchor parser
# keys on) with a CSS background-image holding the event artwork. Reading the
# two together is what lets an image be attached to a specific event.
# The `between` group explicitly cannot span another data-permalink. A
# plain .{0,400}? would let a card that has no image of its own swallow the
# region up to the NEXT card's background-image - and because finditer
# consumes what it matches, that next card would then silently lose its
# artwork rather than merely being mis-paired.
CARD_RE = re.compile(
    r'data-permalink="(?P<path>/events/[^"]+)"'
    r'(?P<between>(?:(?!data-permalink=).){0,400}?)'
    r"background-image:\s*url\('(?P<image>[^']+)'\)",
    re.S,
)


class _AnchorCollector(HTMLParser):
    """Collects every <a href>/<text> pair from an HTML document, in order."""

    def __init__(self):
        super().__init__()
        self.anchors: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._current_href = dict(attrs).get("href")
            self._current_text = []

    def handle_data(self, data):
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._current_href is not None:
            self.anchors.append((self._current_href, "".join(self._current_text).strip()))
            self._current_href = None
            self._current_text = []


class DoTheBayFetcher:
    """Scrapes DoTheBay's daily events listing page.

    FRAGILE - READ BEFORE RELYING ON THIS: DoTheBay (a DoStuff Media property)
    has no public RSS feed or events API - its only "/feed" endpoint is a
    logged-in user's personal activity feed, not an events feed. This fetcher
    parses server-rendered HTML instead, which is inherently less stable than
    an RSS-based source: if DoTheBay changes their page template, this can
    silently return zero or partial results rather than raising an error.

    To minimize (not eliminate) that fragility, parsing depends only on
    anchor href *URL patterns* - "/events/YYYY/M/D/slug" and "/venues/slug" -
    which are part of DoTheBay's routing and much less likely to change than
    CSS classes or DOM nesting. It does not depend on any particular tag
    structure, class name, or attribute beyond href/text on <a> elements.

    Cover images are the exception to that rule, and are held to a lower
    standard on purpose: they come from a `data-permalink` attribute paired
    with a CSS `background-image` on the listing card, which IS markup the
    site can change freely. Images are therefore best-effort - a redesign
    loses the artwork while the events themselves keep parsing.

    Known limitations (by design, not oversight):
    - No time-of-day: DoTheBay doesn't encode event start time in the URL,
      and loose time text like "7:00PM" on the page isn't inside an anchor,
      so there's no reliable generic way to associate it with a specific
      event. Every event's `start` is midnight on the correct date - always
      follow the source link for the actual time.
    - No cost or category data: unlike Funcheap's structured feed, DoTheBay's
      page doesn't expose these as parseable per-event fields here, so
      `cost` and `categories` are left at their defaults ("" and []).
    - The test fixture `dothebay_sample.html` is a HAND-BUILT snippet
      mirroring the URL/anchor patterns, predating any live capture; the
      cover-image fixture alongside it is a real trimmed capture. Verify
      against the live site after any DoTheBay redesign.

    Recommended monitoring: alert if fetch() returns 0 events, since that's
    the most likely symptom of an upstream markup change.
    """

    name = "dothebay"

    def __init__(
        self,
        path: str = "/events/today",
        timeout: int = 15,
        days_ahead: int = 14,
        today: date | None = None,
    ):
        self.path = path
        self.timeout = timeout
        self.days_ahead = days_ahead
        self._today = today

    def fetch(self) -> list[Event]:
        """Walk one page per day.

        `/events/today` only ever returns the current day, and a weekly job
        exporting upcoming events threw almost all of that away as already
        past. Per-date pages (/events/YYYY/M/D) carry the same markup, so
        walking forward a fortnight is the difference between contributing a
        handful of events and contributing a few hundred.
        """
        events: list[Event] = []
        seen: set[str] = set()
        for path in self._paths():
            url = urljoin(BASE_URL, path)
            req = urllib.request.Request(
                url, headers={"User-Agent": "sf-event-curator/0.1"}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            for event in self.parse(html):
                if event.source_id in seen:
                    continue
                seen.add(event.source_id)
                events.append(event)
        return events

    def _paths(self) -> list[str]:
        if self.days_ahead <= 0:
            return [self.path]
        start = self._today or date.today()
        return [
            f"/events/{d.year}/{d.month}/{d.day}"
            for d in (start + timedelta(days=i) for i in range(self.days_ahead))
        ]

    def parse(self, html: str) -> list[Event]:
        images = _cover_images(html)
        collector = _AnchorCollector()
        collector.feed(html)

        events: list[Event] = []
        current: Event | None = None

        for href, text in collector.anchors:
            if not href:
                continue

            event_match = EVENT_HREF_RE.match(href)
            if event_match and text:
                if current is not None:
                    events.append(current)
                year, month, day = (int(g) for g in event_match.groups())
                path = href if href.startswith("/") else "/" + href
                current = Event(
                    source=self.name,
                    source_id=urljoin(BASE_URL, href),
                    title=text,
                    start=datetime(year, month, day),
                    end=None,
                    url=urljoin(BASE_URL, href),
                    images=[images[k] for k in (path, path.rstrip("/")) if k in images][:1],
                )
                continue

            if current is None:
                continue

            if href.startswith("https://maps.google.com/") and not current.address:
                current.address = _address_from_maps_url(href)
            elif VENUE_HREF_RE.match(href) and text and not current.venue:
                current.venue = text

        if current is not None:
            events.append(current)
        return events


def _cover_images(html: str) -> dict[str, str]:
    """Map event path -> artwork URL for every card on the page."""
    out: dict[str, str] = {}
    for m in CARD_RE.finditer(html):
        path = m.group("path")
        out.setdefault(path, m.group("image"))
        out.setdefault(path.rstrip("/"), m.group("image"))
    return out


def _address_from_maps_url(url: str) -> str:
    m = MAPS_QUERY_RE.search(url)
    return unquote(m.group(1)).replace("+", " ") if m else ""
