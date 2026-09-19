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


def test_images_round_trip_as_a_list(tmp_path):
    db = tmp_path / "e.db"
    init_db(db)
    urls = ["https://img/one.jpg", "https://img/two.jpg"]
    upsert_events(db, [make_event("a", images=urls)])
    [r] = [row_to_dict(x) for x in query_events(db)]
    assert r["images"] == urls


def test_no_images_becomes_an_empty_list_not_none(tmp_path):
    """The frontend calls .filter() on this, so None would throw."""
    db = tmp_path / "e.db"
    init_db(db)
    upsert_events(db, [make_event("a")])
    [r] = [row_to_dict(x) for x in query_events(db)]
    assert r["images"] == []


def test_refetching_does_not_wipe_coordinates(tmp_path):
    """Geocoding costs rate-limited requests; a refetch must not discard it."""
    from sf_event_curator.db import set_coordinates

    db = tmp_path / "e.db"
    init_db(db)
    upsert_events(db, [make_event("a")])
    [row] = query_events(db)
    set_coordinates(db, {row["id"]: (37.77, -122.43)})

    upsert_events(db, [make_event("a", title="Renamed")])

    [r] = [row_to_dict(x) for x in query_events(db)]
    assert r["title"] == "Renamed"
    assert (r["lat"], r["lon"]) == (37.77, -122.43)


def test_geocache_stores_hits_and_misses_separately(tmp_path):
    from sf_event_curator.db import get_geocache, geocache_misses, put_geocache

    db = tmp_path / "e.db"
    init_db(db)
    put_geocache(db, "The Independent", 37.77, -122.43, "The Independent, SF")
    put_geocache(db, "TBA", None, None)

    assert get_geocache(db) == {"The Independent": (37.77, -122.43)}
    assert geocache_misses(db) == {"TBA"}


def test_geocache_entries_can_be_corrected(tmp_path):
    from sf_event_curator.db import get_geocache, geocache_misses, put_geocache

    db = tmp_path / "e.db"
    init_db(db)
    put_geocache(db, "Somewhere", None, None)
    assert geocache_misses(db) == {"Somewhere"}
    put_geocache(db, "Somewhere", 1.0, 2.0, "found later")
    assert get_geocache(db) == {"Somewhere": (1.0, 2.0)}
    assert geocache_misses(db) == set()


def test_migration_adds_image_and_coordinate_columns(tmp_path):
    """The same pre-ranking database must also gain these without data loss."""
    import sqlite3

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
            ("test", "legacy-1", "Old event", "2026-01-01T00:00:00"),
        )

    init_db(db)

    [r] = [row_to_dict(x) for x in query_events(db)]
    assert r["title"] == "Old event"
    assert r["images"] == [] and r["lat"] is None and r["lon"] is None


def test_clear_coordinates_blanks_only_the_named_events(tmp_path):
    """A coordinate invalidated later must not stay on the row as a wrong pin."""
    from sf_event_curator.db import clear_coordinates, set_coordinates

    db = tmp_path / "e.db"
    init_db(db)
    upsert_events(db, [make_event("keep"), make_event("drop")])
    ids = {r["source_id"]: r["id"] for r in query_events(db)}
    set_coordinates(db, {
        ids["keep"]: (37.77, -122.43),
        ids["drop"]: (34.05, -118.24),
    })

    assert clear_coordinates(db, [ids["drop"]]) == 1

    by_id = {r["id"]: row_to_dict(r) for r in query_events(db)}
    assert by_id[ids["keep"]]["lat"] == 37.77
    assert by_id[ids["drop"]]["lat"] is None
    assert by_id[ids["drop"]]["lon"] is None


def test_clear_coordinates_with_nothing_to_do_is_a_noop(tmp_path):
    from sf_event_curator.db import clear_coordinates

    db = tmp_path / "e.db"
    init_db(db)
    assert clear_coordinates(db, []) == 0
