from __future__ import annotations

from sfevents import geocode


# ------------------------------ place_key ------------------------------

def test_address_is_preferred_over_venue_name():
    """A street address geocodes far more reliably than a venue name."""
    assert geocode.place_key("The Independent", "628 Divisadero St, SF, CA") == \
        "628 Divisadero St, SF, CA"


def test_venue_is_used_when_there_is_no_address():
    assert geocode.place_key("The Independent", "") == "The Independent"


def test_parenthesised_city_only_address_falls_back_to_venue():
    """19hz writes location as "Venue (City)" with no street address."""
    assert geocode.place_key("Moe's Alley", "(Santa Cruz)") == "Moe's Alley"


def test_a_bare_city_address_is_combined_with_the_venue():
    """Several sources put only a city in the address field.

    Treating that as the answer collapsed every event in a city onto its
    centroid - Golden Gate Park and Ocean Beach both landed downtown.
    """
    assert geocode.place_key("Golden Gate Park", "San Francisco") == \
        "Golden Gate Park, San Francisco"
    assert geocode.place_key("Folsom Street, SoMa", "San Francisco") == \
        "Folsom Street, SoMa, San Francisco"


def test_a_street_address_is_still_used_alone():
    assert geocode.place_key("Fox Theater", "1807 Telegraph Ave, Oakland, CA") == \
        "1807 Telegraph Ave, Oakland, CA"


def test_venue_already_inside_the_address_is_not_repeated():
    assert geocode.place_key("Oakland", "Oakland") == "Oakland"


def test_no_location_at_all_yields_an_empty_key():
    assert geocode.place_key("", "") == ""


# ------------------------------ query_for ------------------------------

def test_trailing_city_is_promoted_into_the_query():
    assert geocode.query_for("1015 Folsom (San Francisco)") == \
        "1015 Folsom, San Francisco, California"


def test_a_bare_city_in_parentheses_becomes_the_query():
    assert geocode.query_for("(Sacramento)") == "Sacramento, California"


def test_california_is_appended_to_disambiguate_venue_names():
    """"The Independent" exists in many countries."""
    assert geocode.query_for("The Independent") == "The Independent, California"


def test_existing_state_is_not_duplicated():
    q = geocode.query_for("Golden Gate Bandshell, San Francisco, CA, 94118")
    assert q.count("California") == 0 and q.endswith("94118")


def test_placeholder_venues_are_never_queried():
    for junk in ["TBA", "tba", "TBD", "", "  ", "various", "Secret Location", "x"]:
        assert geocode.query_for(junk) is None, junk


def test_tba_with_a_region_is_still_skipped():
    assert geocode.query_for("TBA (Mendocino County)") is None


# --------------------------- geocode_places ---------------------------

def fake_lookup(mapping, calls=None):
    def _lookup(query, timeout=20):
        if calls is not None:
            calls.append(query)
        if query in mapping:
            lat, lon = mapping[query]
            return lat, lon, f"display for {query}"
        return None
    return _lookup


def test_resolves_places_and_reports_failures():
    calls = []
    lookup = fake_lookup({"The Independent, California": (37.77, -122.43)}, calls)
    resolved, failed = geocode.geocode_places(
        ["The Independent", "Nowhere Bar"], delay=0, lookup_fn=lookup
    )
    assert resolved == {"The Independent": (37.77, -122.43)}
    assert failed == {"Nowhere Bar"}
    assert len(calls) == 2


def test_cached_places_cost_no_requests():
    """The whole point of the cache: a weekly run should do almost nothing."""
    calls = []
    lookup = fake_lookup({}, calls)
    resolved, failed = geocode.geocode_places(
        ["The Independent"], known={"The Independent": (37.77, -122.43)},
        delay=0, lookup_fn=lookup,
    )
    assert resolved == {} and failed == set()
    assert calls == []


def test_known_bad_places_are_not_retried():
    calls = []
    lookup = fake_lookup({}, calls)
    geocode.geocode_places(
        ["Nowhere Bar"], skip={"Nowhere Bar"}, delay=0, lookup_fn=lookup
    )
    assert calls == []


def test_unqueryable_text_fails_without_spending_a_request():
    calls = []
    lookup = fake_lookup({}, calls)
    resolved, failed = geocode.geocode_places(["TBA"], delay=0, lookup_fn=lookup)
    assert failed == {"TBA"} and calls == []


def test_limit_caps_requests_per_run():
    calls = []
    lookup = fake_lookup({}, calls)
    geocode.geocode_places(
        [f"Venue {i}" for i in range(10)], limit=3, delay=0, lookup_fn=lookup
    )
    assert len(calls) == 3


def test_limit_does_not_count_places_that_were_never_queried():
    """A cap of 2 should buy 2 real lookups, not be eaten by skipped junk."""
    calls = []
    lookup = fake_lookup({}, calls)
    geocode.geocode_places(
        ["TBA", "TBD", "Venue A", "Venue B"], limit=2, delay=0, lookup_fn=lookup
    )
    assert len(calls) == 2


def test_duplicate_places_are_queried_once():
    calls = []
    lookup = fake_lookup({"Venue A, California": (37.77, -122.43)}, calls)
    resolved, _ = geocode.geocode_places(
        ["Venue A", "Venue A", "Venue A"], delay=0, lookup_fn=lookup
    )
    assert len(calls) == 1 and resolved == {"Venue A": (37.77, -122.43)}


def test_a_network_error_fails_only_that_place():
    import urllib.error

    def flaky(query, timeout=20):
        if "Bad" in query:
            raise urllib.error.URLError("boom")
        return 37.77, -122.43, "ok"

    resolved, failed = geocode.geocode_places(
        ["Bad Venue", "Good Venue"], delay=0, lookup_fn=flaky
    )
    assert failed == {"Bad Venue"}
    assert "Good Venue" in resolved


def test_progress_callback_sees_every_place():
    seen = []
    lookup = fake_lookup({"Venue A, California": (37.77, -122.43)})
    geocode.geocode_places(
        ["Venue A", "Nope", "TBA"], delay=0, lookup_fn=lookup,
        on_result=lambda place, coords, note: seen.append((place, coords is not None)),
    )
    assert seen == [("Venue A", True), ("Nope", False), ("TBA", False)]


def test_identifying_user_agent_is_set():
    """Nominatim's usage policy requires a UA that identifies the caller."""
    assert "sfevents" in geocode.USER_AGENT
    assert "github.com" in geocode.USER_AGENT


# --------------------------- region validation ---------------------------

def test_northern_california_places_are_in_region():
    assert geocode.in_region(37.77, -122.43)   # San Francisco
    assert geocode.in_region(37.80, -122.27)   # Oakland
    assert geocode.in_region(38.58, -121.49)   # Sacramento - 19hz lists it
    assert geocode.in_region(36.97, -122.03)   # Santa Cruz
    assert geocode.in_region(39.31, -123.80)   # Mendocino


def test_southern_california_places_are_out_of_region():
    assert not geocode.in_region(34.05, -118.24)   # Los Angeles
    assert not geocode.in_region(33.75, -117.86)   # Orange County
    assert not geocode.in_region(32.72, -117.16)   # San Diego


def test_a_result_outside_the_region_is_rejected_not_stored():
    """A confident answer in the wrong half of the state is worse than none.

    "Union Square Plaza" and "Civic Park East" really did resolve to
    Southern California, putting untrue pins on the map.
    """
    def lookup(query, timeout=20):
        return 34.29, -118.72, "Santa Clarita, California"

    resolved, failed = geocode.geocode_places(
        ["Union Square Plaza"], delay=0, lookup_fn=lookup
    )
    assert resolved == {}
    assert failed == {"Union Square Plaza"}


def test_rejection_reason_is_reported():
    def lookup(query, timeout=20):
        return 34.05, -118.24, "Los Angeles"

    notes = []
    geocode.geocode_places(
        ["Somewhere"], delay=0, lookup_fn=lookup,
        on_result=lambda p, c, note: notes.append(note),
    )
    assert any("outside Northern California" in n for n in notes)


def test_an_in_region_result_is_still_accepted():
    def lookup(query, timeout=20):
        return 37.77, -122.43, "San Francisco"

    resolved, failed = geocode.geocode_places(
        ["Union Square, San Francisco"], delay=0, lookup_fn=lookup
    )
    assert resolved == {"Union Square, San Francisco": (37.77, -122.43)}
    assert not failed
