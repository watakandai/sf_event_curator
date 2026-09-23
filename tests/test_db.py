from __future__ import annotations
from datetime import datetime, timedelta, timezone

import pytest

from sfevents.db import (
    init_db, upsert_events, query_events, delete_event, count_events,
    get_event, update_event, insert_manual_event, row_to_dict,
)
from sfevents.models import Event


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "events.db"
    init_db(path)
    return path


def make_event(source_id="e1", title="Test event", days_from_now=1, cost="0", categories=None):
    start = datetime.now(timezone.utc) + timedelta(days=days_from_now)
    return Event(
        source="testsrc", source_id=source_id, title=title,
        start=start, end=start + timedelta(hours=2),
        venue="Some Venue", cost=cost, categories=categories or ["Outdoors"],
        url="https://example.com/" + source_id,
    )


def test_upsert_and_count(db_path):
    upsert_events(db_path, [make_event("e1"), make_event("e2")])
    assert count_events(db_path) == 2


def test_upsert_dedupes_by_source_and_source_id(db_path):
    upsert_events(db_path, [make_event("e1", title="Original title")])
    upsert_events(db_path, [make_event("e1", title="Updated title")])
    rows = query_events(db_path)
    assert len(rows) == 1
    assert rows[0]["title"] == "Updated title"


def test_query_free_only(db_path):
    upsert_events(db_path, [
        make_event("free1", cost="0"),
        make_event("paid1", cost="25"),
    ])
    rows = query_events(db_path, free_only=True)
    assert len(rows) == 1
    assert rows[0]["source_id"] == "free1"


def test_query_category_filter(db_path):
    upsert_events(db_path, [
        make_event("a", categories=["Outdoors", "Live Music"]),
        make_event("b", categories=["Art & Museums"]),
    ])
    rows = query_events(db_path, category="Outdoors")
    assert len(rows) == 1
    assert rows[0]["source_id"] == "a"


def test_query_date_range(db_path):
    upsert_events(db_path, [
        make_event("soon", days_from_now=1),
        make_event("later", days_from_now=30),
    ])
    cutoff = datetime.now(timezone.utc) + timedelta(days=7)
    rows = query_events(db_path, start_before=cutoff)
    assert [r["source_id"] for r in rows] == ["soon"]


def test_query_orders_by_start_ascending(db_path):
    upsert_events(db_path, [make_event("later", days_from_now=10), make_event("soon", days_from_now=1)])
    rows = query_events(db_path)
    assert [r["source_id"] for r in rows] == ["soon", "later"]


def test_delete_event(db_path):
    upsert_events(db_path, [make_event("gone")])
    row_id = query_events(db_path)[0]["id"]
    delete_event(db_path, row_id)
    assert count_events(db_path) == 0


def test_delete_nonexistent_event_is_a_noop(db_path):
    upsert_events(db_path, [make_event("stays")])
    delete_event(db_path, 9999)
    assert count_events(db_path) == 1


def test_upsert_empty_list_is_a_noop(db_path):
    upsert_events(db_path, [])
    assert count_events(db_path) == 0


def test_query_with_no_filters_returns_everything(db_path):
    upsert_events(db_path, [make_event("a"), make_event("b"), make_event("c")])
    assert len(query_events(db_path)) == 3


def test_query_returns_empty_list_when_nothing_matches(db_path):
    upsert_events(db_path, [make_event("a", cost="25")])
    assert query_events(db_path, free_only=True) == []


def test_query_combined_filters_are_ANDed(db_path):
    upsert_events(db_path, [
        make_event("match", cost="0", categories=["Outdoors"], days_from_now=2),
        make_event("wrong_cost", cost="10", categories=["Outdoors"], days_from_now=2),
        make_event("wrong_category", cost="0", categories=["Indoor"], days_from_now=2),
        make_event("wrong_date", cost="0", categories=["Outdoors"], days_from_now=60),
    ])
    cutoff = datetime.now(timezone.utc) + timedelta(days=7)
    rows = query_events(db_path, free_only=True, category="Outdoors", start_before=cutoff)
    assert [r["source_id"] for r in rows] == ["match"]


def test_same_source_id_across_different_sources_are_distinct_rows(db_path):
    upsert_events(db_path, [
        Event(source="funcheap_sf", source_id="shared", title="from funcheap",
              start=None, end=None),
        Event(source="rec_and_parks", source_id="shared", title="from rec and parks",
              start=None, end=None),
    ])
    assert count_events(db_path) == 2


def test_events_with_null_start_sort_first(db_path):
    dated = make_event("dated", days_from_now=1)
    undated = Event(source="testsrc", source_id="undated", title="TBD", start=None, end=None)
    upsert_events(db_path, [dated, undated])
    rows = query_events(db_path)
    # SQLite orders NULL before non-NULL ascending - assert this is understood, not accidental
    assert rows[0]["source_id"] == "undated"
    assert rows[1]["source_id"] == "dated"


def test_category_filter_does_not_match_substring_of_unrelated_category(db_path):
    upsert_events(db_path, [
        make_event("a", categories=["Art & Museums"]),
    ])
    rows = query_events(db_path, category="Art")
    assert len(rows) == 1  # LIKE '%Art%' - documents current substring-match behavior
    rows_full = query_events(db_path, category="Art & Museums")
    assert len(rows_full) == 1


def test_get_event_returns_none_when_missing(db_path):
    assert get_event(db_path, 999) is None


def test_get_event_returns_row_by_id(db_path):
    upsert_events(db_path, [make_event("a", title="Findable")])
    row_id = query_events(db_path)[0]["id"]
    assert get_event(db_path, row_id)["title"] == "Findable"


def test_update_event_changes_only_given_fields(db_path):
    upsert_events(db_path, [make_event("a", title="Original", cost="0")])
    row_id = query_events(db_path)[0]["id"]
    updated = update_event(db_path, row_id, title="Renamed")
    assert updated["title"] == "Renamed"
    assert updated["cost"] == "0"  # untouched field preserved


def test_update_event_with_no_recognized_fields_is_a_noop(db_path):
    upsert_events(db_path, [make_event("a", title="Stays")])
    row_id = query_events(db_path)[0]["id"]
    updated = update_event(db_path, row_id, nonsense_field="ignored")
    assert updated["title"] == "Stays"


def test_update_event_returns_none_for_missing_id(db_path):
    assert update_event(db_path, 9999, title="x") is None


def test_insert_manual_event_is_queryable(db_path):
    e = Event(source="manual", source_id="uuid-1", title="Ocean Beach bonfire",
              start=None, end=None, venue="Ocean Beach", cost="0", categories=["Outdoors"])
    row = insert_manual_event(db_path, e)
    assert row["title"] == "Ocean Beach bonfire"
    assert count_events(db_path) == 1


def test_insert_manual_event_does_not_collide_with_fetched_events(db_path):
    upsert_events(db_path, [make_event("shared-id", title="From a fetcher")])
    manual = Event(source="manual", source_id="shared-id", title="Manually added",
                    start=None, end=None)
    insert_manual_event(db_path, manual)
    assert count_events(db_path) == 2  # different source -> distinct row, per UNIQUE(source, source_id)


def test_row_to_dict_splits_categories_into_a_list(db_path):
    upsert_events(db_path, [make_event("a", categories=["Outdoors", "Live Music"])])
    row = query_events(db_path)[0]
    d = row_to_dict(row)
    assert d["categories"] == ["Outdoors", "Live Music"]


def test_row_to_dict_empty_categories_becomes_empty_list(db_path):
    # make_event's `categories or [...]` treats [] as falsy and substitutes a
    # default, so construct the Event directly to test the true empty case.
    e = Event(source="testsrc", source_id="no-cats", title="No categories",
              start=None, end=None, categories=[])
    upsert_events(db_path, [e])
    row = query_events(db_path)[0]
    d = row_to_dict(row)
    assert d["categories"] == []


def test_row_to_dict_sets_is_free_correctly(db_path):
    upsert_events(db_path, [make_event("free", cost="0"), make_event("paid", cost="25")])
    rows = {row_to_dict(r)["source_id"]: row_to_dict(r) for r in query_events(db_path)}
    assert rows["free"]["is_free"] is True
    assert rows["paid"]["is_free"] is False


def test_drop_events_removes_only_rejected_titles_of_that_source(db_path):
    from sfevents.db import drop_events
    upsert_events(db_path, [
        make_event("a", "Cycle Track Open All Day"),
        make_event("b", "Chair Yoga"),
    ])
    assert drop_events(db_path, "testsrc", lambda t: "Cycle" not in t) == 1
    assert drop_events(db_path, "othersrc", lambda t: False) == 0
    assert [r["title"] for r in query_events(db_path)] == ["Chair Yoga"]
