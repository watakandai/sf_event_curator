from __future__ import annotations
import importlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from sfevents import db as db_module
from sfevents.models import Event


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient wired to a fresh temp DB, isolated per test."""
    db_path = tmp_path / "events.db"
    monkeypatch.setenv("SFEVENTS_DB", str(db_path))
    # web.py reads the env var at import time, so reload it fresh per test
    from sfevents import web as web_module
    importlib.reload(web_module)
    with TestClient(web_module.app) as c:
        yield c, db_path


def test_index_serves_dashboard_html(client):
    c, _ = client
    res = c.get("/")
    assert res.status_code == 200
    assert "SF Event Curator" in res.text


def test_list_events_empty_db(client):
    c, _ = client
    res = c.get("/api/events")
    assert res.status_code == 200
    assert res.json() == []


def test_create_event_returns_it_with_id(client):
    c, _ = client
    res = c.post("/api/events", json={
        "title": "Ocean Beach bonfire", "venue": "Ocean Beach",
        "cost": "0", "categories": ["Outdoors", "Social"],
    })
    assert res.status_code == 201
    body = res.json()
    assert body["id"] is not None
    assert body["title"] == "Ocean Beach bonfire"
    assert body["categories"] == ["Outdoors", "Social"]
    assert body["is_free"] is True


def test_created_event_is_listable(client):
    c, _ = client
    c.post("/api/events", json={"title": "Bonfire", "cost": "0"})
    res = c.get("/api/events")
    assert len(res.json()) == 1


def test_get_single_event(client):
    c, _ = client
    created = c.post("/api/events", json={"title": "Bonfire", "cost": "0"}).json()
    res = c.get(f"/api/events/{created['id']}")
    assert res.status_code == 200
    assert res.json()["title"] == "Bonfire"


def test_get_missing_event_404s(client):
    c, _ = client
    assert c.get("/api/events/9999").status_code == 404


def test_patch_updates_only_given_fields(client):
    c, _ = client
    created = c.post("/api/events", json={"title": "Original", "venue": "Somewhere", "cost": "0"}).json()
    res = c.patch(f"/api/events/{created['id']}", json={"title": "Renamed"})
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "Renamed"
    assert body["venue"] == "Somewhere"  # untouched


def test_patch_missing_event_404s(client):
    c, _ = client
    res = c.patch("/api/events/9999", json={"title": "x"})
    assert res.status_code == 404


def test_patch_updates_categories_and_dates(client):
    c, _ = client
    created = c.post("/api/events", json={"title": "Bonfire", "cost": "0"}).json()
    start = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    res = c.patch(f"/api/events/{created['id']}", json={
        "start": start.isoformat(), "categories": ["Outdoors", "Bonfire"],
    })
    body = res.json()
    assert body["categories"] == ["Outdoors", "Bonfire"]
    assert body["start_ts"].startswith(start.isoformat()[:16])


def test_delete_event(client):
    c, _ = client
    created = c.post("/api/events", json={"title": "Gone", "cost": "0"}).json()
    res = c.delete(f"/api/events/{created['id']}")
    assert res.status_code == 204
    assert c.get(f"/api/events/{created['id']}").status_code == 404


def test_delete_missing_event_404s(client):
    c, _ = client
    assert c.delete("/api/events/9999").status_code == 404


def test_filter_free_only(client):
    c, _ = client
    c.post("/api/events", json={"title": "Free one", "cost": "0"})
    c.post("/api/events", json={"title": "Paid one", "cost": "25"})
    res = c.get("/api/events", params={"free_only": True})
    titles = [e["title"] for e in res.json()]
    assert titles == ["Free one"]


def test_filter_category(client):
    c, _ = client
    c.post("/api/events", json={"title": "A", "cost": "0", "categories": ["Outdoors"]})
    c.post("/api/events", json={"title": "B", "cost": "0", "categories": ["Indoor"]})
    res = c.get("/api/events", params={"category": "Outdoors"})
    titles = [e["title"] for e in res.json()]
    assert titles == ["A"]


def test_events_from_fetcher_are_also_visible_via_api(client):
    c, db_path = client
    fetched = Event(source="funcheap_sf", source_id="link-1", title="From the feed",
                     start=None, end=None, cost="0")
    db_module.upsert_events(db_path, [fetched])
    titles = [e["title"] for e in c.get("/api/events").json()]
    assert "From the feed" in titles
