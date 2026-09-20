from __future__ import annotations
import calendar
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from ..models import Event

DATA_FILE = Path(__file__).parent.parent / "data" / "annual_events.json"
SATURDAY = 5


class AnnualEventsFetcher:
    """Materializes the curated Bay Area annual-events list into dated events.

    This is the one source that isn't a network fetch. The scraped and RSS
    sources only know about the next week or two, so nothing in them tells you
    in March that Outside Lands is in August or that the Mill Valley Fall Arts
    Festival takes the third weekend of September. This file fills that gap.

    Entries store recurrence RULES, not dates ("last Sunday of September"),
    so the list doesn't need an annual edit. Where a rule only approximates
    real timing - anything tied to a lunar calendar, a league schedule, or an
    organizer's whim - the entry is marked approx and every occurrence it
    produces carries date_approx=True for the UI to flag.

    Occurrences are emitted for a rolling horizon (12 months by default), and
    source_id is "<slug>:<iso date>", so re-running is idempotent and last
    year's occurrences stay put rather than being rewritten.
    """

    name = "annual_bay_area"

    def __init__(
        self,
        data_file: Path | str = DATA_FILE,
        horizon_months: int = 12,
        today: date | None = None,
    ):
        self.data_file = Path(data_file)
        self.horizon_months = horizon_months
        self._today = today  # injectable for tests

    def fetch(self) -> list[Event]:
        spec = json.loads(self.data_file.read_text())
        return self.build(spec["events"])

    def build(self, entries: list[dict]) -> list[Event]:
        today = self._today or date.today()
        horizon_end = _add_months(today, self.horizon_months)

        events: list[Event] = []
        for entry in entries:
            for occ in _occurrences(entry["rule"], today, horizon_end):
                if not (today <= occ <= horizon_end):
                    continue
                span = int(entry["rule"].get("span_days", 1))
                end = occ + timedelta(days=span - 1) if span > 1 else None
                events.append(
                    Event(
                        source=self.name,
                        source_id=f"{entry['id']}:{occ.isoformat()}",
                        title=entry["title"],
                        start=datetime(occ.year, occ.month, occ.day),
                        end=datetime(end.year, end.month, end.day) if end else None,
                        venue=entry.get("venue", ""),
                        address=entry.get("address", "") or entry.get("city", ""),
                        cost=entry.get("cost", ""),
                        categories=list(entry.get("categories", [])),
                        url=entry.get("url", ""),
                        description=entry.get("description", ""),
                        notability=int(entry.get("notability", 0)),
                        date_approx=bool(entry.get("approx", False)),
                    )
                )
        events.sort(key=lambda e: (e.start or datetime.max, e.title))
        return events


def _add_months(d: date, months: int) -> date:
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _months_between(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _occurrences(rule: dict, start: date, end: date) -> list[date]:
    kind = rule["type"]
    out: list[date] = []

    if kind == "monthly_nth_weekday":
        for year, month in _months_between(start, end):
            d = _nth_weekday(year, month, rule["weekday"], rule["n"])
            if d:
                out.append(d)
        return out

    # Every other rule is anchored to one calendar month, so it can fire at
    # most once per year - but the horizon can straddle a year boundary.
    for year in range(start.year, end.year + 1):
        d = _resolve_annual(rule, year)
        if d:
            out.append(d)
    return out


def _resolve_annual(rule: dict, year: int) -> date | None:
    kind = rule["type"]
    month = rule["month"]

    if kind == "fixed":
        return date(year, month, rule["day"])
    if kind == "nth_weekday":
        return _nth_weekday(year, month, rule["weekday"], rule["n"])
    if kind == "nth_weekend":
        return _nth_weekday(year, month, SATURDAY, rule["n"])
    if kind == "last_full_weekend":
        # The last Saturday whose Sunday is still inside the month.
        span = int(rule.get("span_days", 2))
        d = _nth_weekday(year, month, SATURDAY, -1)
        if d and (d + timedelta(days=span - 1)).month != month:
            d -= timedelta(days=7)
        return d
    if kind == "after_nth_weekday":
        base = _nth_weekday(year, month, rule["weekday"], rule["n"])
        return base + timedelta(days=rule["offset_days"]) if base else None
    raise ValueError(f"unknown rule type: {kind!r}")


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date | None:
    """n-th `weekday` of a month; n=-1 means the last one. None if it doesn't exist."""
    days_in_month = calendar.monthrange(year, month)[1]
    matches = [
        date(year, month, day)
        for day in range(1, days_in_month + 1)
        if date(year, month, day).weekday() == weekday
    ]
    if not matches:
        return None
    if n == -1:
        return matches[-1]
    return matches[n - 1] if 1 <= n <= len(matches) else None
