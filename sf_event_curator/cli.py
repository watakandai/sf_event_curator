from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .db import init_db, upsert_events, query_events, row_to_dict
from .fetchers.dothebay import DoTheBayFetcher
from .fetchers.funcheap import FuncheapFetcher

DEFAULT_DB = Path.home() / ".sf_event_curator" / "events.db"
FETCHERS = [FuncheapFetcher(), DoTheBayFetcher()]

# Sources known to be fragile (HTML scraping rather than RSS/API) - a silent
# drop to zero here is the main failure mode worth watching for.
FRAGILE_SOURCES = {"dothebay"}


def main() -> None:
    parser = argparse.ArgumentParser(description="SF event curator")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="pull latest events from all sources into the db")
    sub.add_parser("list", help="print stored events, soonest first")
    export_parser = sub.add_parser(
        "export", help="dump all events to a JSON file (for the static-site build)"
    )
    export_parser.add_argument(
        "--out", required=True, help="output path, e.g. docs/data/events.json"
    )
    args = parser.parse_args()

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    init_db(args.db)

    if args.cmd == "fetch":
        for f in FETCHERS:
            try:
                events = f.fetch()
            except Exception as exc:
                print(f"{f.name}: FAILED ({exc})", file=sys.stderr)
                continue
            upsert_events(args.db, events)
            print(f"{f.name}: {len(events)} events fetched")
            if not events and f.name in FRAGILE_SOURCES:
                print(
                    f"  warning: {f.name} returned 0 events - this source is HTML-scraped "
                    "and may need its parser updated if the site changed.",
                    file=sys.stderr,
                )
    elif args.cmd == "list":
        for row in query_events(args.db):
            when = (row["start_ts"] or "?")[:16].replace("T", " ")
            free = " (free)" if row["cost"] == "0" else ""
            print(f"{when:17} {row['title'][:55]:55} {row['venue']}{free}")
    elif args.cmd == "export":
        events = [row_to_dict(row) for row in query_events(args.db)]
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(events, indent=2, default=str))
        print(f"exported {len(events)} events to {out_path}")


if __name__ == "__main__":
    main()
