from __future__ import annotations
import json
from datetime import date, datetime
from pathlib import Path

from ..models import Event
from .annual import _add_months

DATA_FILE = Path(__file__).parent.parent / "data" / "seasonal.json"


class SeasonalFetcher:
    """Materializes the curated seasonal-activities list into date windows.

    Whale watching, elephant seals, pumpkin patches, the grape crush: these
    aren't events with a date, they're seasons you can go any day within,
    and no event feed lists them. Each entry is a window of month/day pairs
    ("from Dec 15 to Apr 30") that may wrap the new year, and each year's
    window becomes one event with a start and an end. The UI shows them in
    their own "In season" strip rather than on the calendar grid.

    Nature doesn't keep a calendar, so every window is typical timing and
    is marked approximate unless an entry says otherwise (a permit season
    with fixed legal dates, say).
    """

    name = "seasonal_bay_area"

    def __init__(self, data_file: Path | str = DATA_FILE, horizon_months: int = 12,
                 today: date | None = None):
        self.data_file = Path(data_file)
        self.horizon_months = horizon_months
        self._today = today  # injectable for tests

    def fetch(self) -> list[Event]:
        spec = json.loads(self.data_file.read_text())
        return self.build(spec["seasons"])

    def build(self, entries: list[dict]) -> list[Event]:
        today = self._today or date.today()
        horizon_end = _add_months(today, self.horizon_months)
        events: list[Event] = []
        for entry in entries:
            for start, end in windows(entry["from"], entry["to"], today, horizon_end):
                events.append(
                    Event(
                        source=self.name,
                        source_id=f"{entry['id']}:{start.isoformat()}",
                        title=entry["title"],
                        start=datetime(start.year, start.month, start.day),
                        end=datetime(end.year, end.month, end.day),
                        venue=entry.get("venue", ""),
                        address=entry.get("address", "") or entry.get("city", ""),
                        cost=entry.get("cost", ""),
                        categories=["Seasonal"] + list(entry.get("categories", [])),
                        url=entry.get("url", ""),
                        description=entry.get("description", ""),
                        notability=int(entry.get("notability", 0)),
                        date_approx=bool(entry.get("approx", True)),
                    )
                )
        events.sort(key=lambda e: (e.start, e.title))
        return events


def windows(start_md: list[int], end_md: list[int], today: date, horizon_end: date):
    """Each year's (start, end) window that overlaps [today, horizon_end].

    A window whose end month/day falls before its start wraps into the next
    year (Dec 15 -> Apr 30). A window already under way today is kept with
    its real start, so the UI can say "in season since December".
    """
    wraps = tuple(end_md) < tuple(start_md)
    out = []
    for year in range(today.year - 1, horizon_end.year + 1):
        start = date(year, *start_md)
        end = date(year + 1 if wraps else year, *end_md)
        if end >= today and start <= horizon_end:
            out.append((start, end))
    return out
