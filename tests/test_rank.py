from __future__ import annotations
import io
import json

import pytest

from sfevents import rank


def row(**kw):
    base = {
        "id": 1, "source": "dothebay", "title": "A Show", "start_ts": "2026-10-01T20:00:00",
        "end_ts": None, "venue": "", "cost": "", "categories": [], "url": "",
        "description": "", "notability": 0,
    }
    base.update(kw)
    return base


# --------------------------- heuristic ranker ---------------------------

def test_every_row_gets_a_score_in_range():
    rows = [row(id=1), row(id=2, title="Some Festival"), row(id=3, notability=90)]
    scores = rank.heuristic_scores(rows)
    assert set(scores) == {1, 2, 3}
    assert all(0 <= s <= 100 for s, _ in scores.values())


def test_curated_notability_dominates_a_generic_listing():
    rows = [row(id=1, source="annual_bay_area", title="SF Pride Parade", notability=99),
            row(id=2, title="Some DJ Presents Tickets")]
    scores = rank.heuristic_scores(rows)
    assert scores[1][0] > scores[2][0] + 40


def test_marquee_wording_lifts_an_otherwise_plain_listing():
    plain = rank.heuristic_scores([row(id=1, title="Wednesday Night Set")])[1][0]
    fest = rank.heuristic_scores([row(id=1, title="Mission Street Fair")])[1][0]
    assert fest > plain


def test_filler_wording_is_penalised():
    plain = rank.heuristic_scores([row(id=1, title="Kamelot")])[1][0]
    filler = rank.heuristic_scores([row(id=1, title="Goldenvoice Presents Kamelot Tickets")])[1][0]
    assert filler < plain


def test_cross_source_agreement_is_the_strongest_free_signal():
    """Two independent sources listing the same thing is real evidence of scale."""
    alone = rank.heuristic_scores([row(id=1, title="Outside Lands")])[1][0]
    agreed = rank.heuristic_scores([
        row(id=1, source="funcheap_sf", title="Outside Lands"),
        row(id=2, source="dothebay", title="Outside Lands Festival 2026"),
    ])[1][0]
    assert agreed > alone + 10


def test_free_and_multi_day_both_add():
    plain = rank.heuristic_scores([row(id=1)])[1][0]
    free = rank.heuristic_scores([row(id=1, cost="0")])[1][0]
    multi = rank.heuristic_scores([
        row(id=1, start_ts="2026-10-01T12:00:00", end_ts="2026-10-03T12:00:00")
    ])[1][0]
    assert free > plain and multi > plain


def test_high_notability_events_do_not_all_collapse_to_100():
    """Bonuses spend headroom rather than adding, so priors keep their ordering."""
    rows = [
        row(id=1, source="annual_bay_area", title="Big Festival", notability=99, cost="0",
            start_ts="2026-10-01T00:00:00", end_ts="2026-10-03T00:00:00", venue="V", url="u"),
        row(id=2, source="annual_bay_area", title="Bigger Festival", notability=95, cost="0",
            start_ts="2026-10-01T00:00:00", end_ts="2026-10-03T00:00:00", venue="V", url="u"),
    ]
    scores = rank.heuristic_scores(rows)
    assert scores[1][0] <= 100 and scores[2][0] <= 100
    assert scores[1][0] != scores[2][0], "distinct priors must stay distinguishable"


def test_reason_is_always_populated():
    scores = rank.heuristic_scores([row(id=1), row(id=2, notability=80)])
    assert all(reason for _, reason in scores.values())
    assert scores[1][1] == "baseline listing"


def test_heuristic_is_deterministic():
    rows = [row(id=i, title=f"Event {i}") for i in range(20)]
    assert rank.heuristic_scores(rows) == rank.heuristic_scores(rows)


def test_title_key_ignores_punctuation_and_stopwords():
    assert rank.title_key("The Folsom Street Fair!") == rank.title_key("Folsom Street Fair")


# ----------------------------- LLM ranker ------------------------------

def stub_provider(monkeypatch, reply, env="STUB_KEY"):
    calls = []

    def call(prompt, model, key, timeout):
        calls.append({"prompt": prompt, "model": model, "key": key})
        return reply(prompt) if callable(reply) else reply

    monkeypatch.setitem(rank.PROVIDERS, "stub", (env, "stub-model", call))
    monkeypatch.setenv(env, "test-key")
    return calls


def test_parse_scores_tolerates_prose_and_code_fences():
    text = 'Sure!\n```json\n[{"i":1,"score":80,"reason":"outdoor music"}]\n```\nHope that helps.'
    assert rank.parse_scores(text, 1) == {1: (80.0, "outdoor music")}


def test_parse_scores_clamps_and_drops_out_of_range_indices():
    text = json.dumps([
        {"i": 1, "score": 150, "reason": "over"},
        {"i": 9, "score": 50, "reason": "not in this batch"},
        {"i": 2, "score": -5, "reason": "under"},
    ])
    assert rank.parse_scores(text, 2) == {1: (100.0, "over"), 2: (0.0, "under")}


def test_parse_scores_skips_malformed_entries_rather_than_failing():
    text = json.dumps([{"i": 1, "score": "abc"}, {"i": 2, "score": 40, "reason": "ok"}])
    assert rank.parse_scores(text, 2) == {2: (40.0, "ok")}


def test_parse_scores_without_an_array_is_an_error():
    with pytest.raises(ValueError, match="no JSON array"):
        rank.parse_scores("I cannot help with that.", 3)


def test_llm_scores_maps_batch_indices_back_to_event_ids(monkeypatch):
    stub_provider(monkeypatch, json.dumps([
        {"i": 1, "score": 10, "reason": "a"}, {"i": 2, "score": 20, "reason": "b"},
    ]))
    rows = [row(id=101), row(id=202)]
    out = rank.llm_scores(rows, "profile", provider="stub", batch_size=2)
    assert out == {101: (10.0, "a"), 202: (20.0, "b")}


def test_llm_scores_batches_and_restarts_numbering_each_batch(monkeypatch):
    calls = stub_provider(monkeypatch, json.dumps([{"i": 1, "score": 55, "reason": "x"}]))
    rows = [row(id=1), row(id=2), row(id=3)]
    out = rank.llm_scores(rows, "profile", provider="stub", batch_size=1)
    assert len(calls) == 3
    assert out == {1: (55.0, "x"), 2: (55.0, "x"), 3: (55.0, "x")}


def test_one_failing_batch_does_not_lose_the_others(monkeypatch):
    def reply(prompt):
        if "Bad Event" in prompt:
            raise ValueError("model hiccup")
        return json.dumps([{"i": 1, "score": 42, "reason": "fine"}])

    stub_provider(monkeypatch, reply)
    rows = [row(id=1, title="Good Event"), row(id=2, title="Bad Event")]
    notes = []
    out = rank.llm_scores(rows, "p", provider="stub", batch_size=1,
                          on_progress=lambda o, s, n: notes.append(n))
    assert out == {1: (42.0, "fine")}
    assert any("FAILED" in n for n in notes)


def test_profile_text_is_actually_sent(monkeypatch):
    calls = stub_provider(monkeypatch, json.dumps([{"i": 1, "score": 1, "reason": "r"}]))
    rank.llm_scores([row(id=1)], "I only like free outdoor techno.", provider="stub")
    assert "I only like free outdoor techno." in calls[0]["prompt"]


def test_long_descriptions_are_truncated_before_being_sent(monkeypatch):
    calls = stub_provider(monkeypatch, json.dumps([{"i": 1, "score": 1, "reason": "r"}]))
    rank.llm_scores([row(id=1, description="x" * 5000)], "p", provider="stub")
    assert "x" * 200 not in calls[0]["prompt"]


def test_missing_api_key_raises_a_clear_error(monkeypatch):
    stub_provider(monkeypatch, "[]")
    monkeypatch.delenv("STUB_KEY")
    with pytest.raises(RuntimeError, match="STUB_KEY is not set"):
        rank.llm_scores([row(id=1)], "p", provider="stub")


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="unknown provider"):
        rank.llm_scores([row(id=1)], "p", provider="nope")


def test_all_shipped_providers_are_well_formed():
    """Adding a provider must stay a one-liner: env var, default model, callable."""
    assert {"anthropic", "gemini"} <= set(rank.PROVIDERS)
    for name, (env, model, call) in rank.PROVIDERS.items():
        assert env and model and callable(call), name


def test_profile_hash_changes_with_profile_and_model():
    a = rank.profile_hash("likes techno", "model-1")
    assert a == rank.profile_hash("likes techno", "model-1")
    assert a != rank.profile_hash("likes techno ", "model-1")
    assert a != rank.profile_hash("likes techno", "model-2")


def test_missing_profile_file_explains_the_fix(tmp_path):
    with pytest.raises(FileNotFoundError, match="profile.example.md"):
        rank.load_profile(tmp_path / "nope.md")


def test_shipped_profile_loads():
    assert len(rank.load_profile()) > 100


# --------------------- cross-source matching specifics ---------------------

def test_differently_spelled_titles_still_count_as_the_same_event():
    agreement = rank.agreeing_sources([
        row(id=1, source="funcheap_sf", title="Outside Lands"),
        row(id=2, source="dothebay", title="Outside Lands Festival 2026"),
    ])
    assert agreement[1] == {"funcheap_sf", "dothebay"}


def test_a_shared_prefix_is_not_treated_as_the_same_event():
    """A run of Bandshell concerts shares a prefix without being one event."""
    agreement = rank.agreeing_sources([
        row(id=1, source="sfrecpark", title="Golden Gate Bandshell: Friday Happy Hour"),
        row(id=2, source="19hz_bayarea", title="Golden Gate Bandshell: Crucial Reggae Sunday"),
    ])
    assert agreement[1] == {"sfrecpark"}
    assert agreement[2] == {"19hz_bayarea"}


def test_duplicates_within_one_source_do_not_fake_agreement():
    agreement = rank.agreeing_sources([
        row(id=1, source="19hz_bayarea", title="Some Warehouse Party"),
        row(id=2, source="19hz_bayarea", title="Some Warehouse Party"),
    ])
    assert agreement[1] == {"19hz_bayarea"}


def test_single_word_titles_are_not_matched():
    agreement = rank.agreeing_sources([
        row(id=1, source="a", title="Kamelot"),
        row(id=2, source="b", title="Kamelot"),
    ])
    assert 1 not in agreement  # too little to go on


def test_agreement_scales_the_bonus_but_is_capped():
    one = rank.heuristic_scores([row(id=1, source="a", title="Big Street Fair")])[1][0]
    three = rank.heuristic_scores([
        row(id=1, source="a", title="Big Street Fair"),
        row(id=2, source="b", title="Big Street Fair"),
        row(id=3, source="c", title="Big Street Fair"),
    ])[1][0]
    assert three > one
    assert three - one <= 20 + 1  # capped contribution


def test_editing_notes_are_not_sent_to_the_model(tmp_path):
    """profile.md is a prompt: guidance for the reader must not become facts."""
    p = tmp_path / "profile.md"
    p.write_text(
        "# Me\n\n"
        "<!--\nTO FILL IN: what you like.\nThis repo is public.\n-->\n\n"
        "- I like art festivals.\n"
    )
    loaded = rank.load_profile(p)
    assert "TO FILL IN" not in loaded
    assert "public" not in loaded
    assert "I like art festivals." in loaded


def test_shipped_profile_has_no_leftover_comment_markers():
    loaded = rank.load_profile()
    assert "<!--" not in loaded and "-->" not in loaded


def test_http_errors_carry_the_provider_message(monkeypatch):
    """A bare status code can't tell you if the key, model or payload is wrong."""
    import urllib.error

    body = b'{"error":{"code":400,"message":"API key not valid","status":"INVALID_ARGUMENT"}}'

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {}, io.BytesIO(body)
        )

    monkeypatch.setattr(rank.urllib.request, "urlopen", boom)
    with pytest.raises(rank.ProviderError, match="API key not valid"):
        rank._post_json("https://example.test/v1", {}, {"a": 1}, 30)


def test_a_key_in_the_url_is_never_echoed_into_the_error(monkeypatch):
    import urllib.error

    body = b'{"error":{"message":"bad request for key=SUPERSECRET"}}'

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {}, io.BytesIO(body)
        )

    monkeypatch.setattr(rank.urllib.request, "urlopen", boom)
    with pytest.raises(rank.ProviderError) as exc:
        rank._post_json("https://example.test/v1?key=SUPERSECRET", {}, {}, 30)
    assert "SUPERSECRET" not in str(exc.value)


def test_a_failed_batch_reports_the_reason_and_spares_the_others(monkeypatch):
    """Per-batch isolation must survive the richer error type."""
    calls = {"n": 0}

    def flaky(prompt, model, key, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise rank.ProviderError("HTTP 400: quota exhausted")
        return json.dumps([{"i": 1, "score": 50, "reason": "ok"}])

    monkeypatch.setitem(rank.PROVIDERS, "stub", ("STUB_KEY", "m", flaky))
    monkeypatch.setenv("STUB_KEY", "x")
    notes: list[str] = []
    rows = [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]

    out = rank.llm_scores(rows, "profile", provider="stub", batch_size=1,
                          on_progress=lambda o, s, note: notes.append(note))

    assert "quota exhausted" in notes[0]
    assert set(out) == {2}


def test_a_rate_limit_carries_the_wait_the_provider_asked_for(monkeypatch):
    """Gemini puts the wait in the body, not a header."""
    import urllib.error

    body = b'{"error":{"code":429,"details":[{"retryDelay":"37s"}]}}'

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, io.BytesIO(body))

    monkeypatch.setattr(rank.urllib.request, "urlopen", boom)
    with pytest.raises(rank.ProviderError) as info:
        rank._post_json("https://example.test/v1", {}, {"a": 1}, 30)
    assert info.value.status == 429
    assert info.value.retry_after == 37.0


def _stub(monkeypatch, fn):
    monkeypatch.setitem(rank.PROVIDERS, "stub", ("STUB_KEY", "m", fn))
    monkeypatch.setenv("STUB_KEY", "x")


def test_a_per_minute_limit_is_waited_out_not_lost(monkeypatch):
    calls = {"n": 0}

    def limited_once(prompt, model, key, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise rank.ProviderError("HTTP 429: slow down", status=429, retry_after=20)
        return json.dumps([{"i": 1, "score": 50, "reason": "ok"}])

    _stub(monkeypatch, limited_once)
    waits: list[float] = []
    rows = [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]

    out = rank.llm_scores(rows, "profile", provider="stub", batch_size=1, sleep=waits.append)

    assert set(out) == {1, 2}
    assert waits == [20]


def test_a_quota_that_never_clears_stops_the_run(monkeypatch):
    """Past the retries it's a daily quota: don't burn calls on every batch."""
    calls = {"n": 0}

    def always_limited(prompt, model, key, timeout):
        calls["n"] += 1
        raise rank.ProviderError("HTTP 429: quota", status=429)

    _stub(monkeypatch, always_limited)
    notes: list[str] = []
    rows = [{"id": i, "title": str(i)} for i in range(3)]

    out = rank.llm_scores(rows, "profile", provider="stub", batch_size=1,
                          sleep=lambda s: None,
                          on_progress=lambda o, s, note: notes.append(note))

    assert out == {}
    assert calls["n"] == len(rank.RETRY_WAITS) + 1
    assert notes[1:] == ["skipped (rate limited)"] * 2


def test_a_dropped_connection_is_retried_like_a_503(monkeypatch):
    """Seen in Actions: 'Connection reset by peer' mid-run lost a whole batch."""
    import urllib.error
    calls = {"n": 0}

    def reset_once(prompt, model, key, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError(ConnectionResetError(104, "Connection reset by peer"))
        return json.dumps([{"i": 1, "score": 50, "reason": "ok"}])

    _stub(monkeypatch, reset_once)
    out = rank.llm_scores([{"id": 1, "title": "A"}], "profile", provider="stub",
                          sleep=lambda s: None)
    assert out == {1: (50.0, "ok")}


def test_a_timeout_fails_its_batch_rather_than_the_run(monkeypatch):
    def always_slow(prompt, model, key, timeout):
        raise TimeoutError("read timed out")

    _stub(monkeypatch, always_slow)
    notes: list[str] = []
    out = rank.llm_scores([{"id": 1, "title": "A"}], "profile", provider="stub",
                          sleep=lambda s: None,
                          on_progress=lambda o, s, note: notes.append(note))
    assert out == {}
    assert notes[0].startswith("FAILED (TimeoutError")


def test_other_errors_are_not_retried(monkeypatch):
    calls = {"n": 0}

    def bad_request(prompt, model, key, timeout):
        calls["n"] += 1
        raise rank.ProviderError("HTTP 400: bad", status=400)

    _stub(monkeypatch, bad_request)
    rank.llm_scores([{"id": 1, "title": "A"}], "profile", provider="stub",
                    sleep=lambda s: pytest.fail("should not wait"))
    assert calls["n"] == 1


def test_a_daily_quota_is_named_and_not_retried(monkeypatch):
    """Retrying a per-day quota only spends more of tomorrow's allowance."""
    import urllib.error

    body = (b'{"error":{"code":429,"message":"' + b"x" * 500 + b'","details":[{"violations":'
            b'[{"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},'
            b'{"retryDelay":"30s"}]}}')
    calls = {"n": 0}

    def boom(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, io.BytesIO(body))

    monkeypatch.setattr(rank.urllib.request, "urlopen", boom)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    notes: list[str] = []
    rows = [{"id": i, "title": str(i)} for i in range(3)]

    out = rank.llm_scores(rows, "profile", provider="gemini", batch_size=1,
                          sleep=lambda s: None,
                          on_progress=lambda o, s, note: notes.append(note))

    assert out == {}
    assert calls["n"] == 1
    assert "GenerateRequestsPerDayPerProjectPerModel-FreeTier" in notes[0]
    assert notes[1:] == ["skipped (rate limited)"] * 2


def test_min_interval_spaces_out_requests(monkeypatch):
    now = {"t": 0.0}
    sent: list[float] = []

    def reply(prompt, model, key, timeout):
        sent.append(now["t"])
        now["t"] += 4  # each request takes 4s
        return json.dumps([{"i": 1, "score": 50, "reason": "ok"}])

    def sleep(s):
        now["t"] += s

    _stub(monkeypatch, reply)
    rows = [{"id": i, "title": str(i)} for i in range(3)]

    out = rank.llm_scores(rows, "profile", provider="stub", batch_size=1,
                          min_interval=15, sleep=sleep, clock=lambda: now["t"])

    assert len(out) == 3
    assert sent == [0, 15, 30]


def test_the_llm_sees_where_an_event_is_and_how_long_it_runs():
    line = rank._event_line(1, {
        "title": "Gray whales", "start_ts": "2026-12-15T00:00:00",
        "end_ts": "2027-04-30T00:00:00", "venue": "Lighthouse", "address": "Inverness",
    })
    assert "until=2027-04-30" in line
    assert "address=Inverness" in line


# ------------------------------ fallbacks -------------------------------

def _scores_all(prompt, model, key, timeout):
    import re
    batch = re.findall(r"^(\d+)\.\s", prompt, re.MULTILINE)
    return json.dumps([{"i": int(i), "score": 70, "reason": model} for i in batch])


def _unavailable(prompt, model, key, timeout):
    raise rank.ProviderError("HTTP 503: high demand", status=503)


def _two_providers(monkeypatch, primary, fallback, fallback_key="y"):
    monkeypatch.setitem(rank.PROVIDERS, "first", ("FIRST_KEY", "first-m", primary))
    monkeypatch.setitem(rank.PROVIDERS, "backup", ("BACKUP_KEY", "backup-m", fallback))
    monkeypatch.setitem(rank.FALLBACK_PACING, "backup", (2, 30.0))
    monkeypatch.setenv("FIRST_KEY", "x")
    if fallback_key:
        monkeypatch.setenv("BACKUP_KEY", fallback_key)
    else:
        monkeypatch.delenv("BACKUP_KEY", raising=False)
    monkeypatch.setattr(rank, "RETRY_WAITS", (0, 0, 0))


def test_what_the_primary_misses_goes_to_the_fallback(monkeypatch):
    """The Actions failure this exists for: Gemini 503'd every batch."""
    _two_providers(monkeypatch, _unavailable, _scores_all)
    rows = [{"id": i, "title": str(i)} for i in range(1, 5)]

    out = rank.llm_scores_chain(rows, "p", ["first", "backup"], sleep=lambda s: None)

    assert set(out) == {"backup:backup-m"}
    assert set(out["backup:backup-m"]) == {1, 2, 3, 4}


def test_the_fallback_only_sees_events_the_primary_missed(monkeypatch):
    calls = {"n": 0}

    def first_batch_only(prompt, model, key, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps([{"i": 1, "score": 10, "reason": "ok"},
                               {"i": 2, "score": 20, "reason": "ok"}])
        raise rank.ProviderError("HTTP 400: bad", status=400)

    sent: list[str] = []

    def backup(prompt, model, key, timeout):
        sent.append(prompt)
        return _scores_all(prompt, model, key, timeout)

    _two_providers(monkeypatch, first_batch_only, backup)
    rows = [{"id": i, "title": f"Event{i}"} for i in range(1, 5)]

    out = rank.llm_scores_chain(rows, "p", ["first", "backup"], batch_size=2,
                                sleep=lambda s: None)

    assert set(out["first:first-m"]) == {1, 2}
    assert set(out["backup:backup-m"]) == {3, 4}
    assert all("Event1" not in p and "Event2" not in p for p in sent)


def test_the_fallback_is_paced_by_its_own_limits(monkeypatch):
    """Groq caps tokens per minute: small batches, spaced out."""
    _two_providers(monkeypatch, _unavailable, _scores_all)
    waits: list[float] = []
    clock = iter(range(0, 1000, 1))
    rows = [{"id": i, "title": str(i)} for i in range(1, 7)]

    out = rank.llm_scores_chain(rows, "p", ["first", "backup"], batch_size=50,
                                sleep=waits.append, clock=lambda: next(clock))

    assert len(out["backup:backup-m"]) == 6
    # 3 batches of 2; the 2nd and 3rd wait out the 30s spacing.
    assert [w for w in waits if w > 0] == [29, 29]


def test_a_fallback_without_a_key_is_skipped_not_fatal(monkeypatch):
    _two_providers(monkeypatch, _unavailable, _scores_all, fallback_key=None)
    notes: list[str] = []

    out = rank.llm_scores_chain([{"id": 1, "title": "A"}], "p", ["first", "backup"],
                                sleep=lambda s: None,
                                on_provider=lambda n, m, k, note: notes.append(note))

    assert out == {}
    assert "BACKUP_KEY not set" in notes[-1]


def test_nothing_goes_to_the_fallback_when_the_primary_succeeds(monkeypatch):
    _two_providers(monkeypatch, _scores_all,
                   lambda *a: pytest.fail("fallback should not be called"))
    out = rank.llm_scores_chain([{"id": 1, "title": "A"}], "p", ["first", "backup"])
    assert set(out) == {"first:first-m"}


def test_groq_sends_an_openai_style_request(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        captured["body"] = json.loads(req.data)
        reply = {"choices": [{"message": {"content": '[{"i":1,"score":5,"reason":"x"}]'}}]}
        return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setattr(rank.urllib.request, "urlopen", fake_urlopen)
    text = rank._call_groq("hello", rank.GROQ_MODEL, "gsk_test", 30)

    assert text.startswith("[")
    assert captured["url"] == rank.GROQ_URL
    assert captured["auth"] == "Bearer gsk_test"
    assert captured["body"]["model"] == "openai/gpt-oss-120b"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hello"}]


def test_a_groq_daily_limit_is_recognised(monkeypatch):
    import urllib.error

    body = (b'{"error":{"message":"Rate limit reached for model openai/gpt-oss-120b '
            b'on tokens per day (TPD): Limit 200000, Used 199000"}}')

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, io.BytesIO(body))

    monkeypatch.setattr(rank.urllib.request, "urlopen", boom)
    with pytest.raises(rank.ProviderError) as info:
        rank._post_json(rank.GROQ_URL, {}, {}, 30)
    assert info.value.daily


def test_ollama_asks_for_a_big_enough_context_and_no_thinking(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        captured["body"] = json.loads(req.data)
        captured["timeout"] = timeout
        reply = {"message": {"role": "assistant", "content": '[{"i":1,"score":5,"reason":"x"}]'}}
        return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setattr(rank.urllib.request, "urlopen", fake_urlopen)
    text = rank._call_ollama("hello", rank.OLLAMA_MODEL, "127.0.0.1:11434", 600)

    assert text.startswith("[")
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["auth"] is None
    body = captured["body"]
    assert body["stream"] is False and body["think"] is False
    assert body["options"]["num_ctx"] >= 8192


def test_ollama_is_the_last_resort_and_gets_a_long_timeout(monkeypatch):
    """Gemini and Groq both down: the local model still scores everything."""
    timeouts: list[int] = []

    def local(prompt, model, host, timeout):
        timeouts.append(timeout)
        return _scores_all(prompt, model, host, timeout)

    _two_providers(monkeypatch, _unavailable, _unavailable)
    monkeypatch.setitem(rank.PROVIDERS, "ollama", ("OLLAMA_HOST", "tiny", local))
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    rows = [{"id": i, "title": str(i)} for i in range(1, 13)]

    out = rank.llm_scores_chain(rows, "p", ["first", "backup", "ollama"],
                                sleep=lambda s: None)

    assert set(out) == {"ollama:tiny"}
    assert len(out["ollama:tiny"]) == 12
    assert timeouts == [600, 600]  # two batches of 10 and 2


def test_ollama_is_skipped_when_no_server_was_started(monkeypatch):
    _two_providers(monkeypatch, _unavailable, _unavailable)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    notes: list[str] = []
    out = rank.llm_scores_chain([{"id": 1, "title": "A"}], "p",
                                ["first", "backup", "ollama"], sleep=lambda s: None,
                                on_provider=lambda n, m, k, note: notes.append(note))
    assert out == {}
    assert "OLLAMA_HOST not set" in notes[-1]


def test_requests_do_not_use_urllibs_default_user_agent(monkeypatch):
    """Groq's Cloudflare front door 403s "Python-urllib/3.x" (error 1010)."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["ua"] = req.get_header("User-agent")
        return io.BytesIO(b"{}")

    monkeypatch.setattr(rank.urllib.request, "urlopen", fake_urlopen)
    rank._post_json(rank.GROQ_URL, {"Authorization": "Bearer x"}, {}, 30)
    assert captured["ua"] and "Python-urllib" not in captured["ua"]
