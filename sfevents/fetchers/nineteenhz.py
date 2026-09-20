from __future__ import annotations
import re
import urllib.request
from datetime import datetime
from html.parser import HTMLParser

from ..models import Event

LISTING_URL = "https://19hz.info/eventlisting_BayArea.php"
SORT_DATE_RE = re.compile(r"(\d{4})/(\d{2})/(\d{2})")
TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])m", re.I)
VENUE_SPLIT_RE = re.compile(r"\s+@\s+")
FREE_RE = re.compile(r"\bfree\b", re.I)
PRICE_RE = re.compile(r"[$\u00a3\u20ac]|\bdonation\b|\bnotaflof\b", re.I)
AGE_ONLY_RE = re.compile(r"\d{2}\+|all ages", re.I)

# Column order in the listing table.
COL_WHEN, COL_TITLE, COL_TAGS, COL_PRICE, COL_ORGANIZER, COL_LINKS, COL_SORT = range(7)


class _RowParser(HTMLParser):
    """Reads the listing table into rows of (text, first_href) per cell.

    The page's markup is not well-formed - title cells are frequently left
    unclosed before the next <td> opens - so this treats any <td> start as
    closing the previous cell instead of trusting </td>. That is also why
    this can't be done with a regex over </td>: the closing tags aren't
    reliably there.
    """

    def __init__(self):
        super().__init__()
        self.rows: list[list[tuple[str, str]]] = []
        self._row: list[tuple[str, str]] | None = None
        self._text: list[str] = []
        self._href = ""
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._flush_row()
            self._row = []
        elif tag in ("td", "th"):
            self._flush_cell()
            self._in_cell = True
        elif tag == "a" and self._in_cell and not self._href:
            self._href = dict(attrs).get("href") or ""
        elif tag == "br" and self._in_cell:
            self._text.append(" ")

    def handle_endtag(self, tag):
        if tag == "tr":
            self._flush_row()

    def handle_data(self, data):
        if self._in_cell:
            self._text.append(data)

    def _flush_cell(self):
        if self._in_cell and self._row is not None:
            text = re.sub(r"\s+", " ", "".join(self._text)).strip()
            self._row.append((text, self._href))
        self._text = []
        self._href = ""
        self._in_cell = False

    def _flush_row(self):
        self._flush_cell()
        if self._row:
            self.rows.append(self._row)
        self._row = None

    def close(self):
        super().close()
        self._flush_row()


class NineteenHzFetcher:
    """Fetches 19hz.info's Bay Area listing - electronic music and club nights.

    19hz is a hand-maintained listing that covers the club/warehouse/festival
    side of the Bay far more completely than the general-interest sources,
    several hundred events deep and months ahead. It is also the only source
    here that reliably carries BOTH a real start time and a ticket price.

    FRAGILE: this is HTML scraping with no API behind it, and the page's
    markup is invalid in places (see _RowParser). Parsing is anchored to
    column order and to the hidden sortable date column the page uses for
    its own sorting, which is more stable than its inline formatting - but a
    redesign will still break it. A drop to zero events is the symptom.

    Genre tags become categories, so "techno" or "house" work as category
    filters alongside the Funcheap vocabulary.
    """

    name = "19hz_bayarea"

    def __init__(self, listing_url: str = LISTING_URL, timeout: int = 25):
        self.listing_url = listing_url
        self.timeout = timeout

    def fetch(self) -> list[Event]:
        req = urllib.request.Request(
            self.listing_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; sfevents/0.1)"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        return self.parse(html)

    def parse(self, html: str) -> list[Event]:
        parser = _RowParser()
        parser.feed(html)
        parser.close()

        events: list[Event] = []
        seen: set[str] = set()
        for row in parser.rows:
            if len(row) < COL_SORT + 1:
                continue
            start = _row_start(row)
            if start is None:
                continue
            title, venue = _split_title_venue(row[COL_TITLE][0])
            if not title:
                continue

            url = row[COL_TITLE][1] or row[COL_LINKS][1] or self.listing_url
            source_id = url if row[COL_TITLE][1] else f"{start.date().isoformat()}|{title}"
            if source_id in seen:
                continue
            seen.add(source_id)

            price = row[COL_PRICE][0]
            events.append(
                Event(
                    source=self.name,
                    source_id=source_id,
                    title=title,
                    start=start,
                    end=None,
                    venue=venue,
                    address="",
                    cost="0" if FREE_RE.search(price) else _price(price),
                    categories=_tags(row[COL_TAGS][0]),
                    url=url,
                    description=row[COL_ORGANIZER][0],
                )
            )
        return events


def _row_start(row: list[tuple[str, str]]) -> datetime | None:
    """Date from the hidden sort column, time-of-day from the display column."""
    m = SORT_DATE_RE.search(row[COL_SORT][0])
    if not m:
        return None
    year, month, day = (int(g) for g in m.groups())
    hour, minute = _first_time(row[COL_WHEN][0])
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def _first_time(text: str) -> tuple[int, int]:
    m = TIME_RE.search(text)
    if not m:
        return (0, 0)
    hour = int(m.group(1)) % 12
    if m.group(3).lower() == "p":
        hour += 12
    return (hour, int(m.group(2) or 0))


def _split_title_venue(text: str) -> tuple[str, str]:
    parts = VENUE_SPLIT_RE.split(text, maxsplit=1)
    title = parts[0].strip()
    venue = parts[1].strip() if len(parts) > 1 else ""
    # The tags column often bleeds into this cell through unclosed markup.
    venue = venue.split("|")[0].strip()
    return title, venue


def _price(text: str) -> str:
    """First segment of the "Price | Age" cell, but only if it really is a price.

    The two halves are not reliably both present: a row with no listed price
    but an age limit reads just "21+", which must not be recorded as costing
    21 dollars.
    """
    head = text.split("|")[0].strip()
    if not head or AGE_ONLY_RE.fullmatch(head):
        return ""
    return head if PRICE_RE.search(head) else ""


def _tags(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()][:6]
