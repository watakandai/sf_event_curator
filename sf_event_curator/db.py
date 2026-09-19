from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    start_ts TEXT,
    end_ts TEXT,
    venue TEXT,
    address TEXT,
    cost TEXT,
    categories TEXT,
    url TEXT,
    description TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_ts);
"""


@contextmanager
def connect(db_path: str | Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str | Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def upsert_events(db_path: str | Path, events: list[Event]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        for e in events:
            conn.execute(
                """INSERT INTO events
                   (source, source_id, title, start_ts, end_ts, venue, address,
                    cost, categories, url, description, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source, source_id) DO UPDATE SET
                     title=excluded.title, start_ts=excluded.start_ts, end_ts=excluded.end_ts,
                     venue=excluded.venue, address=excluded.address, cost=excluded.cost,
                     categories=excluded.categories, url=excluded.url,
                     description=excluded.description, fetched_at=excluded.fetched_at""",
                (
                    e.source, e.source_id, e.title,
                    e.start.isoformat() if e.start else None,
                    e.end.isoformat() if e.end else None,
                    e.venue, e.address, e.cost, ",".join(e.categories),
                    e.url, e.description, now,
                ),
            )
    return len(events)


def query_events(
    db_path: str | Path,
    *,
    start_after: datetime | None = None,
    start_before: datetime | None = None,
    free_only: bool = False,
    category: str | None = None,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM events WHERE 1=1"
    params: list = []
    if start_after:
        sql += " AND start_ts >= ?"
        params.append(start_after.isoformat())
    if start_before:
        sql += " AND start_ts <= ?"
        params.append(start_before.isoformat())
    if free_only:
        sql += " AND cost = '0'"
    if category:
        sql += " AND categories LIKE ?"
        params.append(f"%{category}%")
    sql += " ORDER BY start_ts ASC"
    with connect(db_path) as conn:
        return conn.execute(sql, params).fetchall()


def delete_event(db_path: str | Path, event_id: int) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))


def get_event(db_path: str | Path, event_id: int) -> sqlite3.Row | None:
    with connect(db_path) as conn:
        return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


UPDATABLE_FIELDS = {
    "title", "start_ts", "end_ts", "venue", "address",
    "cost", "categories", "url", "description",
}


def update_event(db_path: str | Path, event_id: int, **fields) -> sqlite3.Row | None:
    """Partial update. Unknown keys are ignored; only UPDATABLE_FIELDS are written."""
    changes = {k: v for k, v in fields.items() if k in UPDATABLE_FIELDS}
    if not changes:
        return get_event(db_path, event_id)
    set_clause = ", ".join(f"{k} = ?" for k in changes)
    with connect(db_path) as conn:
        conn.execute(
            f"UPDATE events SET {set_clause} WHERE id = ?",
            (*changes.values(), event_id),
        )
    return get_event(db_path, event_id)


def insert_manual_event(db_path: str | Path, event: Event) -> sqlite3.Row:
    """Insert a single user-created event (e.g. an Ocean Beach bonfire) and return the stored row."""
    upsert_events(db_path, [event])
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM events WHERE source = ? AND source_id = ?",
            (event.source, event.source_id),
        ).fetchone()


def count_events(db_path: str | Path) -> int:
    with connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def row_to_dict(row: sqlite3.Row) -> dict:
    """Shared shape for API responses and the static-site JSON export."""
    d = dict(row)
    d["categories"] = [c for c in (d["categories"] or "").split(",") if c]
    d["is_free"] = d["cost"] == "0"
    return d
