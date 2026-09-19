"""Turning venue strings into coordinates, so the calendar can show a map.

Events arrive with human place text ("The Independent", "1015 Folsom
(San Francisco)") and no coordinates. A map needs numbers.

The whole design is shaped by one fact: venues repeat enormously. About 300
distinct places cover ~1000 events, and those places don't change between
weekly runs. So geocoding is keyed by place rather than by event and cached
in the database permanently - the first run does real work, later runs do
almost none, and the map itself needs no geocoding at all because
coordinates ship in the export.

Uses Nominatim (OpenStreetMap), which needs no API key but asks for at most
one request per second and a User-Agent that identifies the caller. Both are
honoured here, and `limit` caps how much any single run will do. Failures are
cached too, so an unparseable venue string isn't re-queried every week. If
this ever needs to run at real scale, a paid geocoder is the answer rather
than leaning harder on a donated service.
"""
from __future__ import annotations
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "sf-event-curator/0.1 (+https://github.com/watakandai/sf_event_curator)"

# Bay Area bounding box (west, north, east, south), used to prefer local
# matches - "The Independent" and "1015 Folsom" are ambiguous worldwide.
VIEWBOX = "-123.2,38.5,-121.5,36.8"

# A viewbox without bounded=1 is only a *preference*, and generic venue names
# happily match the wrong end of the state: "Union Square Plaza" and "Civic
# Park East" both resolved to Southern California. Rather than hard-bounding
# the query - which would throw away the legitimately-outside places 19hz
# lists, like Sacramento and Mendocino - results are accepted only if they
# land in Northern California: roughly Monterey up to the Oregon border,
# coast across to Tahoe.
REGION_BOUNDS = (35.5, 42.1, -124.6, -119.0)  # min lat, max lat, min lon, max lon


def in_region(lat: float, lon: float) -> bool:
    min_lat, max_lat, min_lon, max_lon = REGION_BOUNDS
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon

# Trailing "(City)" is how 19hz writes location; promote it to a real city
# so the geocoder sees "1015 Folsom, San Francisco" rather than parentheses.
TRAILING_CITY_RE = re.compile(r"^(?P<place>.*?)\s*\((?P<city>[^)]+)\)\s*$")
UNRESOLVABLE = {"", "tba", "tbd", "secret location", "various", "citywide"}


HAS_STREET_NUMBER_RE = re.compile(r"\d")


def place_key(venue: str, address: str) -> str:
    """Cache key for an event's location: the most specific text available.

    A real street address wins outright - it geocodes far more reliably than
    a venue name. But several sources put only a CITY in the address field,
    and treating that as the answer collapsed every event in a city onto its
    centroid: 36 annual events, Golden Gate Park and Ocean Beach among them,
    all landed on the same point in downtown San Francisco. When the address
    carries no street number, the venue is the specific part and the address
    is the disambiguator, so they're combined.
    """
    venue = (venue or "").strip()
    address = (address or "").strip()
    if address.startswith("("):
        address = ""  # 19hz writes "(City)" with no street address
    if not address:
        return venue
    if not venue or HAS_STREET_NUMBER_RE.search(address):
        return address
    if venue.lower() in address.lower():
        return address
    return f"{venue}, {address}"


def query_for(place: str) -> str | None:
    """Search string for a place, or None if it's not worth a request."""
    place = (place or "").strip()
    if place.lower() in UNRESOLVABLE or len(place) < 3:
        return None
    m = TRAILING_CITY_RE.match(place)
    if m:
        inner, city = m.group("place").strip(), m.group("city").strip()
        if not inner:
            place = city
        else:
            place = f"{inner}, {city}"
    if "TBA" in place or "TBD" in place:
        return None
    # Keep results in California; venue names alone are globally ambiguous.
    if not re.search(r"\b(ca|california)\b", place, re.I):
        place = f"{place}, California"
    return place


def lookup(query: str, timeout: int = 20) -> tuple[float, float, str] | None:
    """One Nominatim lookup. Returns (lat, lon, display_name) or None."""
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "limit": "1",
        "countrycodes": "us",
        "viewbox": VIEWBOX,
    })
    req = urllib.request.Request(
        f"{NOMINATIM_URL}?{params}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        results = json.loads(resp.read().decode())
    if not results:
        return None
    top = results[0]
    try:
        return float(top["lat"]), float(top["lon"]), top.get("display_name", "")
    except (KeyError, TypeError, ValueError):
        return None


def geocode_places(
    places: list[str],
    *,
    known: dict[str, tuple[float, float]] | None = None,
    skip: set[str] | None = None,
    limit: int = 0,
    delay: float = 1.0,
    on_result=None,
    lookup_fn=lookup,
) -> tuple[dict[str, tuple[float, float]], set[str]]:
    """Resolve places not already known.

    Returns (resolved, failed). Sleeps `delay` between real requests to stay
    inside Nominatim's one-per-second limit; `limit` caps requests per run so
    a first run against a large backlog can be spread over several runs.
    """
    known = known or {}
    skip = skip or set()
    resolved: dict[str, tuple[float, float]] = {}
    failed: set[str] = set()
    requests_made = 0

    for place in places:
        if place in known or place in skip or place in resolved or place in failed:
            continue
        query = query_for(place)
        if query is None:
            failed.add(place)
            if on_result:
                on_result(place, None, "unresolvable text, not queried")
            continue
        if limit and requests_made >= limit:
            break

        if requests_made and delay:
            time.sleep(delay)
        requests_made += 1
        try:
            hit = lookup_fn(query)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
            failed.add(place)
            if on_result:
                on_result(place, None, f"FAILED ({type(exc).__name__}: {exc})")
            continue

        if hit is None:
            failed.add(place)
            if on_result:
                on_result(place, None, "no match")
            continue
        lat, lon, display = hit
        if not in_region(lat, lon):
            # A confident answer in the wrong half of the state is worse than
            # no answer: it puts a pin on the map that is simply untrue.
            failed.add(place)
            if on_result:
                on_result(place, None, f"rejected, outside Northern California ({lat:.2f},{lon:.2f})")
            continue
        resolved[place] = (lat, lon)
        if on_result:
            on_result(place, (lat, lon), display)
    return resolved, failed
