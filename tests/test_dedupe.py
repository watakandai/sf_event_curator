from __future__ import annotations

from sfevents.dedupe import dedupe, same_event


def row(title, start="2026-10-03T00:00:00", venue="", source="dothebay", key=None, **kw):
    r = {
        "title": title, "start_ts": start, "end_ts": None, "venue": venue,
        "source": source, "address": "", "cost": "", "url": "", "description": "",
        "images": [], "categories": [], "lat": None, "lon": None, "score": None,
        "key": key or f"{source}:{title}:{start}",
    }
    r.update(kw)
    return r


def test_the_same_show_on_two_sources_becomes_one_card():
    out = dedupe([
        row("Kreayshawn", venue="DNA Lounge"),
        row("Kreayshawn", "2026-10-03T21:30:00", "DNA Lounge (San Francisco)", "19hz_bayarea"),
    ])
    assert len(out) == 1


def test_a_presenter_prefix_does_not_hide_a_duplicate():
    assert same_event(
        row("Goldenvoice Presents: Portola Week 2026\n    Fatboy Slim", venue="888 Garage"),
        row("Fatboy Slim", "2026-10-03T22:00:00", "888 Garage (San Francisco)", "19hz_bayarea"),
    )


def test_one_source_listing_a_show_twice_is_merged():
    out = dedupe([
        row("Heather McDonald", venue="Cobb's Comedy Club", key="dothebay:a"),
        row("Heather McDonald", venue="Cobb's Comedy Club", key="dothebay:b"),
    ])
    assert len(out) == 1


def test_different_days_are_never_merged():
    assert len(dedupe([
        row("Spyro Gyra", "2026-09-30T00:00:00", "Yoshi's"),
        row("Spyro Gyra", "2026-10-01T00:00:00", "Yoshi's"),
    ])) == 2


def test_same_name_at_different_venues_is_kept_apart():
    assert len(dedupe([
        row("Bilal", venue="August Hall"),
        row("Bilal", venue="Rickshaw Stop"),
    ])) == 2


def test_a_one_word_title_needs_both_venues_to_agree():
    assert len(dedupe([
        row("Halloween", "2026-10-31T00:00:00", "Citywide", "annual_bay_area"),
        row("Halloween", "2026-10-31T06:00:00", "", "visit_sausalito"),
    ])) == 2


def test_an_after_party_is_not_the_festival():
    assert not same_event(
        row("Castro Street Fair", "2026-10-04T00:00:00", "Castro District", "annual_bay_area"),
        row("Castro Street Fair After Party Ft Octo Octa", "2026-10-04T18:00:00",
            "The Castro", "19hz_bayarea"),
    )


def test_the_best_scored_copy_wins_and_borrows_what_it_lacks():
    out = dedupe([
        row("Kreayshawn", venue="DNA Lounge", score=80.0, key="dothebay:k"),
        row("Kreayshawn", "2026-10-03T21:30:00", "DNA Lounge", "19hz_bayarea",
            score=60.0, url="https://dnalounge.com", key="19hz_bayarea:k"),
    ])
    (card,) = out
    assert card["key"] == "dothebay:k"
    assert card["url"] == "https://dnalounge.com"
    assert card["start_ts"] == "2026-10-03T21:30:00"  # date-only borrows the time
    assert card["also"] == ["19hz_bayarea:k"]  # plans pinned to the other copy still resolve


def test_order_and_undated_rows_are_preserved():
    rows = [row("B thing"), row("A thing", None), row("B thing", key="x")]
    assert [r["title"] for r in dedupe(rows)] == ["B thing", "A thing"]


def test_a_year_suffix_and_a_street_abbreviation_do_not_split_an_event():
    assert same_event(
        row("Folsom Street Fair", "2026-09-27T00:00:00", "Folsom Street, SoMa", "annual_bay_area"),
        row("Folsom Street Fair 2026", "2026-09-27T00:00:00", "Folsom St"),
    )
