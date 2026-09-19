from __future__ import annotations
import sqlite3
from datetime import datetime

from sf_event_curator.db import (
    SCHEMA, init_db, upsert_events, query_events, set_scores, unscored_events,
    row_to_dict, count_events,
)
from sf_event_curator.models import Event


def make_event(source_id: str, **kw) -> Event:
    base = dict(
        source="test", source_id=source_id, title=f"Event {source_id}",
        start=datetime(2026, 10, 1, 19, 0), end=None,
    )
    base.update(kw)
    return Event(**base)


def test_new_columns_exist_after_init(tmp_path):
    db = tmp_path / "e.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    assert {"notability", "date_approx", "score", "score_reason",
            "scored_by", "scored_at", "profile_hash"} <= cols


def test_init_db_migrates_a_pre_ranking_database(tmp_path):
    """Existing installs must not have to delete and re-fetch their database."""
    db = tmp_path / "old.db"
    legacy = """CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL, source_id TEXT NOT NULL, title TEXT NOT NULL,
        start_ts TEXT, end_ts TEXT, venue TEXT, address TEXT, cost TEXT,
        categories TEXT, url TEXT, description TEXT, fetched_at TEXT NOT NULL,
        UNIQUE(source, source_id));"""
    with sqlite3.connect(db) as conn:
        conn.executescript(legacy)
        conn.execute(
            "INSERT INTO events (source, source_id, title, fetched_at) VALUES (?,?,?,?)",
            ("test", "legacy-1", "Pre-existing event", "2026-01-01T00:00:00"),
        )

    init_db(db)  # must add columns without losing the row

    assert count_events(db) == 1
    [rowdict] = [row_to_dict(r) for r in query_events(db)]
    assert rowdict["title"] == "Pre-existing event"
    assert rowdict["score"] is None
    assert rowdict["notability"] == 0


def test_init_db_is_idempotent(tmp_path):
    db = tmp_path / "e.db"
    init_db(db)
    init_db(db)
    init_db(db)
    assert count_events(db) == 0


def test_notability_and_date_approx_round_trip(tmp_path):
    db = tmp_path / "e.db"
    init_db(db)
    upsert_events(db, [make_event("a", notability=77, date_approx=True)])
    [r] = [row_to_dict(x) for x in query_events(db)]
    assert r["notability"] == 77
    assert r["date_approx"] is True


def test_date_approx_is_exposed_as_a_bool_not_an_int(tmp_path):
    """The frontend branches on this, so 0/1 would be a silent truthiness bug."""
    db = tmp_path / "e.db"
    init_db(db)
    upsert_events(db, [make_event("a", date_approx=False)])
    [r] = [row_to_dict(x) for x in query_events(db)]
    assert r["date_approx"] is False


def test_refetching_does_not_wipe_an_existing_score(tmp_path):
    """Scores are computed separately from fetching; a weekly refetch must keep them."""
    db = tmp_path / "e.db"
    init_db(db)
    upsert_events(db, [make_event("a")])
    [row] = query_events(db)
    set_scores(db, {row["id"]: (88.0, "good")}, scored_by="heuristic", profile_hash="h1")

    upsert_events(db, [make_event("a", title="Event a (renamed)")])

    [row] = [row_to_dict(r) for r in query_events(db)]
    assert row["title"] == "Event a (renamed)"
    assert row["score"] == 88.0
    assert row["score_reason"] == "good"


def test_set_scores_records_provenance(tmp_path):
    db = tmp_path / "e.db"
    init_db(db)
    upsert_events(db, [make_event("a")])
    [row] = query_events(db)
    set_scores(db, {row["id"]: (50.0, "why")}, scored_by="gemini:gemini-2.5-flash",
               profile_hash="abc123")
    [r] = [row_to_dict(x) for x in query_events(db)]
    assert r["scored_by"] == "gemini:gemini-2.5-flash"
    assert r["profile_hash"] == "abc123"
    assert r["scored_at"]


def test_order_by_score_puts_best_first_and_unscored_last(tmp_path):
    db = tmp_path / "e.db"
    init_db(db)
    upsert_events(db, [make_event("a"), make_event("b"), make_event("c")])
    ids = [r["id"] for r in query_events(db)]
    set_scores(db, {ids[0]: (10.0, ""), ids[1]: (90.0, "")},
               scored_by="heuristic", profile_hash="")

    ordered = query_events(db, order_by="score")
    assert [r["score"] for r in ordered] == [90.0, 10.0, None]


def test_order_by_date_is_still_the_default(tmp_path):
    db = tmp_path / "e.db"
    init_db(db)
    upsert_events(db, [
        make_event("late", start=datetime(2026, 12, 1)),
        make_event("early", start=datetime(2026, 10, 1)),
    ])
    assert [r["source_id"] for r in query_events(db)] == ["early", "late"]


def test_unknown_order_by_is_rejected(tmp_path):
    import pytest
    db = tmp_path / "e.db"
    init_db(db)
    with pytest.raises(ValueError, match="unknown order_by"):
        query_events(db, order_by="popularity")


def test_unscored_events_finds_rows_needing_work(tmp_path):
    db = tmp_path / "e.db"
    init_db(db)
    upsert_events(db, [make_event("a"), make_event("b")])
    ids = [r["id"] for r in query_events(db)]
    set_scores(db, {ids[0]: (50.0, "")}, scored_by="llm", profile_hash="h1")

    todo = [r["id"] for r in unscored_events(db, "h1")]
    assert todo == [ids[1]]


def test_editing_the_profile_marks_everything_for_rescoring(tmp_path):
    """profile_hash changing is how a profile.md edit invalidates old scores."""
    db = tmp_path / "e.db"
    init_db(db)
    upsert_events(db, [make_event("a"), make_event("b")])
    ids = [r["id"] for r in query_events(db)]
    set_scores(db, {i: (50.0, "") for i in ids}, scored_by="llm", profile_hash="old")

    assert unscored_events(db, "old") == []
    assert len(unscored_events(db, "new-profile-hash")) == 2


def test_schema_declares_a_score_index():
    assert "idx_events_score" in SCHEMA
