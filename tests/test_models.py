from __future__ import annotations
from datetime import datetime

from sfevents.models import Event


def make_event(cost: str) -> Event:
    return Event(
        source="s", source_id="1", title="t",
        start=datetime(2026, 1, 1), end=None, cost=cost,
    )


def test_is_free_true_for_zero_cost():
    assert make_event("0").is_free


def test_is_free_false_for_nonzero_cost():
    assert not make_event("25").is_free


def test_is_free_strips_whitespace():
    assert make_event("  0  ").is_free


def test_is_free_false_for_empty_cost():
    assert not make_event("").is_free


def test_categories_default_to_empty_list_and_are_independent():
    e1 = Event(source="s", source_id="1", title="a", start=None, end=None)
    e2 = Event(source="s", source_id="2", title="b", start=None, end=None)
    e1.categories.append("Outdoors")
    assert e2.categories == []  # dataclass field(default_factory) isn't shared
