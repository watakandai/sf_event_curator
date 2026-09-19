from __future__ import annotations
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

from ..models import Event

FEED_URL = "https://sf.funcheap.com/rssfeed2/"
NS = {"funCheap": "https://sf.funcheap.com/rssfeed/"}


class FuncheapFetcher:
    """Fetches SF Funcheap's events RSS feed.

    The feed carries a custom funCheap:* namespace with structured fields
    (startTime, endTime, cost, venue, categories) beyond plain RSS, which is
    why this is a dedicated parser rather than a generic RSS reader.
    """

    name = "funcheap_sf"

    def __init__(self, feed_url: str = FEED_URL, timeout: int = 15):
        self.feed_url = feed_url
        self.timeout = timeout

    def fetch(self) -> list[Event]:
        req = urllib.request.Request(
            self.feed_url, headers={"User-Agent": "sf-event-curator/0.1"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = resp.read()
        return self.parse(data)

    def parse(self, xml_bytes: bytes) -> list[Event]:
        root = ET.fromstring(xml_bytes)
        events = []
        for item in root.findall("./channel/item"):
            link = _text(item, "link")
            if not link:
                continue
            events.append(
                Event(
                    source=self.name,
                    source_id=link,
                    title=_text(item, "title"),
                    start=_dt(item, "funCheap:startTime"),
                    end=_dt(item, "funCheap:endTime"),
                    venue=_text(item, "funCheap:venue"),
                    address=_text(item, "funCheap:venueAddress"),
                    cost=_text(item, "funCheap:cost"),
                    categories=_categories(item),
                    url=_text(item, "funCheap:url") or link,
                    description=_text(item, "description"),
                    images=_images(item),
                )
            )
        return events


def _text(item: ET.Element, tag: str) -> str:
    el = item.find(tag, NS)
    return (el.text or "").strip() if el is not None and el.text else ""


def _dt(item: ET.Element, tag: str) -> datetime | None:
    el = item.find(tag, NS)
    if el is None or not el.text:
        return None
    try:
        return parsedate_to_datetime(el.text.strip())
    except (TypeError, ValueError):
        return None


# The feed carries each event's artwork twice: the original upload and a
# 170x170 thumbnail of the same file. They're one image, not two, so the
# thumbnail is dropped rather than padding out a carousel with duplicates.
THUMB_MARKER = "/thumbnails/"


def _images(item: ET.Element) -> list[str]:
    urls = []
    for enc in item.findall("enclosure"):
        url = (enc.get("url") or "").strip()
        if url and THUMB_MARKER not in url and url not in urls:
            urls.append(url)
    return urls


def _categories(item: ET.Element) -> list[str]:
    raw = _text(item, "funCheap:categories")
    return [c.strip() for c in raw.split(",") if c.strip()]
