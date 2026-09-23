from __future__ import annotations
import re
import urllib.request
from datetime import date, datetime
from html.parser import HTMLParser

from ..models import Event

BASE_URL = "https://sfrecpark.org"
CALENDAR_PATH = "/Calendar.aspx"
EID_RE = re.compile(r"[?&]EID=(\d+)")
EVENT_ITEMTYPE = "schema.org/Event"
NESTED_ITEMTYPES = ("schema.org/Place", "schema.org/PostalAddress")

# The calendar mixes in listings nobody would go to: facility hours ("Cycle
# Track Open After 6:45 PM", dozens a month) and the department's own
# governance meetings. They aren't events, and a model scoring them against
# "likes cycling" happily ranks them near the top.
NOT_AN_EVENT_RE = re.compile(
    r"^cycle track (open|closed)\b"
    r"|\bcommittee\b|\bcommission meeting\b|^prosac$",
    re.IGNORECASE,
)


def is_event(title: str) -> bool:
    return not NOT_AN_EVENT_RE.search(title.strip())


class _MicrodataParser(HTMLParser):
    """Pulls schema.org/Event microdata records out of the calendar page.

    The page embeds a hidden microdata block per event alongside the visible
    markup. Reading the microdata rather than the visible DOM is the whole
    point: itemprop names are a published vocabulary the site has to keep
    stable for search engines, whereas its CSS classes and nesting are free
    to change in any redesign.

    Nested scopes matter here - an Event has a `name` and so does its
    `location` Place - so itemprops resolve against a scope stack rather than
    a flat dict.
    """

    def __init__(self):
        super().__init__()
        self.records: list[dict] = []
        self._scopes: list[dict] = []   # innermost last
        self._depth = 0
        self._event_depth: int | None = None
        self._prop: str | None = None
        self._prop_depth: int | None = None
        self._buf: list[str] = []
        self._pending_eid_for: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self._depth += 1

        # A "More Details" link follows each event block and carries its EID.
        if tag == "a" and self._pending_eid_for is not None:
            m = EID_RE.search(a.get("href") or "")
            if m:
                self._pending_eid_for["eid"] = m.group(1)
                self._pending_eid_for = None

        itemtype = a.get("itemtype") or ""
        if "itemscope" in a and EVENT_ITEMTYPE in itemtype:
            record: dict = {}
            self.records.append(record)
            self._scopes = [record]
            self._event_depth = self._depth
            return

        if not self._scopes:
            return

        if "itemscope" in a and any(t in itemtype for t in NESTED_ITEMTYPES):
            nested: dict = {}
            prop = a.get("itemprop")
            # location/address hang off the scope that contains them.
            self._scopes[-1].setdefault(prop or "_nested", nested)
            self._scopes.append(nested)
            return

        if a.get("itemprop") and self._prop is None:
            self._prop = a["itemprop"]
            self._prop_depth = self._depth
            self._buf = []

    def handle_data(self, data):
        if self._prop is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if self._prop is not None and self._depth == self._prop_depth:
            if self._scopes:
                self._scopes[-1][self._prop] = "".join(self._buf).strip()
            self._prop = None
            self._prop_depth = None
            self._buf = []

        if len(self._scopes) > 1 and self._depth == self._event_depth + len(self._scopes) - 1:
            self._scopes.pop()
        elif self._scopes and self._event_depth == self._depth:
            self._pending_eid_for = self._scopes[0]
            self._scopes = []
            self._event_depth = None

        self._depth -= 1


class SFRecParkFetcher:
    """Fetches the SF Recreation & Parks department event calendar.

    Complements the nightlife-heavy sources: this is where the free,
    daytime, all-ages side of the city shows up - Bandshell concerts, park
    volunteer days, rec-center programs, movie nights.

    The calendar is a classic server-rendered ASP.NET page, one month per
    request, so `months_ahead` controls how far forward to walk. Unlike the
    DoTheBay scraper, events here carry a real ISO `startDate` with a
    time-of-day, plus a full street address.

    Cost is not exposed in the microdata; most listings are free but some
    ticketed programs are not, so cost is left empty rather than guessed.
    """

    name = "sfrecpark"
    is_event = staticmethod(is_event)

    def __init__(self, months_ahead: int = 3, timeout: int = 20, today: date | None = None):
        self.months_ahead = months_ahead
        self.timeout = timeout
        self._today = today

    def fetch(self) -> list[Event]:
        start = self._today or date.today()
        events: list[Event] = []
        seen: set[str] = set()
        for year, month in _month_window(start, self.months_ahead):
            url = f"{BASE_URL}{CALENDAR_PATH}?month={month}&year={year}&calType=0"
            req = urllib.request.Request(
                url, headers={"User-Agent": "sfevents/0.1"}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            for event in self.parse(html):
                if event.source_id in seen:
                    continue
                seen.add(event.source_id)
                events.append(event)
        return events

    def parse(self, html: str) -> list[Event]:
        parser = _MicrodataParser()
        parser.feed(html)

        events: list[Event] = []
        for rec in parser.records:
            title = (rec.get("name") or "").strip()
            start = _parse_iso(rec.get("startDate"))
            if not title or start is None or not is_event(title):
                continue
            place = rec.get("location") or {}
            addr = place.get("address") or {}
            eid = rec.get("eid")
            events.append(
                Event(
                    source=self.name,
                    source_id=eid or f"{title}|{start.isoformat()}",
                    title=title,
                    start=start,
                    end=_parse_iso(rec.get("endDate")),
                    venue=(place.get("name") or "").strip(),
                    address=_join_address(addr),
                    cost="",
                    categories=["Parks & Rec"],
                    url=f"{BASE_URL}{CALENDAR_PATH}?EID={eid}" if eid else BASE_URL,
                    description=(rec.get("description") or "").strip(),
                )
            )
        return events


def _month_window(start: date, months: int):
    y, m = start.year, start.month
    for _ in range(max(1, months)):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _join_address(addr: dict) -> str:
    parts = [
        addr.get("streetAddress", ""),
        addr.get("addressLocality", ""),
        addr.get("addressRegion", ""),
        addr.get("postalCode", ""),
    ]
    return ", ".join(p.strip() for p in parts if p and p.strip())
