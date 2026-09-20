"""Scoring events, so the front page isn't just "whatever got scraped last".

Two rankers, deliberately layered:

- `heuristic_scores` is deterministic, offline and free. It runs every time
  and answers "how big a deal is this, generally?" using signals that don't
  need a model: the curated notability prior, cross-source agreement, marquee
  event words, free-ness, data completeness.

- `llm_scores` answers the different and more useful question: "would THIS
  person want to go?", reading a plain-English profile the user maintains in
  profile.md. It needs a key and costs money, so it's opt-in, batched, and cached by profile revision - only
  events that have never been scored against the current profile are sent.
  Which model does the rating is a swappable provider - Claude or Gemini -
  see PROVIDERS.

Keeping both matters: the heuristic is the floor when there's no key, no
network, or a rate limit, and it's what the LLM's output gets sanity-checked
against.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_PROFILE = Path(__file__).parent.parent / "profile.md"

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-5"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODEL = "gemini-3.6-flash"

# Words that mark an event as a civic/large-scale occasion rather than a
# routine listing. Deliberately about the KIND of event, not its quality.
MARQUEE_RE = re.compile(
    r"\b(festival|parade|fair|fireworks|marathon|carnaval|pride|"
    r"street fair|block party|air show|home opener|opening night|powwow|"
    r"lighting|new year)\b",
    re.I,
)
# Boilerplate that inflates scraped titles without saying anything.
FILLER_RE = re.compile(
    r"\b(presents?|tickets?|tour \d{4}|w/|feat\.?|featuring|"
    r"pop-?up|happy hour|open mic|trivia|karaoke)\b",
    re.I,
)
STOPWORDS = {"the", "a", "an", "of", "at", "in", "on", "and", "presents", "present"}

SOURCE_WEIGHT = {
    "annual_bay_area": 0,   # already carries an explicit notability prior
    "seasonal_bay_area": 0, # likewise
    "santacruz_org": 2,     # official visitor bureau: parks tours, festivals
    "visit_sausalito": 1,
    "bodega_bay": 0,        # mostly restaurant and bar nights
    "funcheap_sf": 4,       # human-curated, structured, free-leaning
    "sfrecpark": 2,         # official, all-ages, reliably real
    "19hz_bayarea": 0,
    "dothebay": -2,         # no cost/category data, date-only
    "manual": 6,            # the user typed it in themselves
}


# --------------------------------------------------------------------------
# heuristic ranker
# --------------------------------------------------------------------------

def title_tokens(title: str) -> list[str]:
    """Content words of a title, lowercased, punctuation and stopwords dropped."""
    return [w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in STOPWORDS]


def title_key(title: str) -> str:
    """Normalized title, for coarse grouping and for readable test assertions."""
    return " ".join(title_tokens(title)[:4])


def agreeing_sources(rows: list[dict]) -> dict[int, set[str]]:
    """Which sources appear to be listing the same event, per row.

    Two sources rarely spell an event identically - "Outside Lands" versus
    "Outside Lands Festival 2026", "SF Pride Parade" versus "San Francisco
    Pride Parade & Celebration" - so exact-key matching misses precisely the
    cases this signal exists to catch.

    Instead, titles are bucketed by their first two content words to keep the
    comparison cheap, and within a bucket two titles are treated as the same
    event when one's word set contains the other's. Subset containment rather
    than shared prefix matters: a whole run of "Golden Gate Bandshell: ..."
    concerts shares a prefix without being the same event, and would otherwise
    all read as corroborated.
    """
    buckets: dict[str, list[tuple[dict, set[str]]]] = {}
    for r in rows:
        tokens = title_tokens(r["title"] or "")
        if len(tokens) < 2:
            continue  # one-word titles match far too much to be evidence
        buckets.setdefault(" ".join(tokens[:2]), []).append((r, set(tokens)))

    out: dict[int, set[str]] = {}
    for group in buckets.values():
        for r, tokens in group:
            sources = {r["source"]}
            for other, other_tokens in group:
                if other is r:
                    continue
                if tokens <= other_tokens or other_tokens <= tokens:
                    sources.add(other["source"])
            out[r["id"]] = sources
    return out


def heuristic_scores(rows: list[dict]) -> dict[int, tuple[float, str]]:
    """Score every row 0-100 on general notability. Pure function of the input."""
    # Cross-source agreement: if two independent sources list the same event,
    # that is the strongest free popularity signal available here.
    agreement = agreeing_sources(rows)

    out: dict[int, tuple[float, str]] = {}
    for r in rows:
        notability = int(r.get("notability") or 0)
        why: list[str] = ["curated annual event"] if notability else []

        bonus = 0.0
        title = r["title"] or ""
        if MARQUEE_RE.search(title):
            bonus += 14
            why.append("festival/parade scale")
        if FILLER_RE.search(title):
            bonus -= 8

        agreeing = len(agreement.get(r["id"], ()))
        if agreeing > 1:
            bonus += min(20, 15 * (agreeing - 1))
            why.append(f"listed by {agreeing} sources")

        if r.get("end_ts") and r.get("start_ts") and r["end_ts"][:10] > r["start_ts"][:10]:
            bonus += 6
            why.append("multi-day")
        if (r.get("cost") or "").strip() == "0":
            bonus += 6
            why.append("free")

        filled = sum(bool((r.get(f) or "").strip()) for f in ("venue", "cost", "url"))
        if r.get("categories"):
            filled += 1
        bonus += filled  # up to +4 for a fully-populated listing
        bonus += SOURCE_WEIGHT.get(r["source"], 0)

        if notability:
            # Spend the bonus on the headroom left above the prior rather than
            # adding into it. Adding saturated the 0-100 clamp and collapsed
            # every marquee event to exactly 100, throwing away the ordering
            # the priors exist to provide.
            headroom = 100.0 - notability
            score = notability + bonus * headroom / 100.0
        else:
            score = 30.0 + bonus

        score = max(0.0, min(100.0, score))
        out[r["id"]] = (round(score, 1), ", ".join(why[:3]) or "baseline listing")
    return out


# --------------------------------------------------------------------------
# LLM ranker
# --------------------------------------------------------------------------

def load_profile(path: Path | str = DEFAULT_PROFILE) -> str:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(
            f"no profile at {p}. Copy profile.example.md to profile.md and edit it - "
            "the LLM ranker needs to know who it's ranking for."
        )
    return strip_comments(p.read_text())


def strip_comments(text: str) -> str:
    """Drop HTML comments so editing notes never reach the model.

    profile.md is a prompt, not just documentation: every word in it is sent
    to the ranker. Guidance for the human reader ("edit this", "here's what
    to include") would otherwise be read as facts about the person.
    """
    without = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return re.sub(r"\n{3,}", "\n\n", without).strip()


def profile_hash(profile: str, model: str) -> str:
    """Identifies a (profile, model) pair, so edits to either force a re-rank."""
    return hashlib.sha256(f"{model}\x00{profile}".encode()).hexdigest()[:16]


def _event_line(i: int, r: dict) -> str:
    bits = [f"{i}. {r['title']}"]
    if r.get("start_ts"):
        bits.append(f"when={r['start_ts'][:16].replace('T', ' ')}")
        # Seasons and festivals run for days or months; say so, or a whale
        # season reads as a one-day event.
        end = (r.get("end_ts") or "")[:10]
        if end and end > r["start_ts"][:10]:
            bits.append(f"until={end}")
    # The address carries the town - the difference between a bar in the
    # Mission and a day trip to Mendocino.
    for field in ("venue", "address", "cost"):
        if (r.get(field) or "").strip():
            bits.append(f"{field}={r[field]}")
    cats = r.get("categories")
    if cats:
        bits.append("tags=" + ",".join(cats if isinstance(cats, list) else [cats]))
    desc = re.sub(r"\s+", " ", (r.get("description") or ""))[:160]
    if desc:
        bits.append(f"note={desc}")
    return " | ".join(bits)


PROMPT = """You are ranking Bay Area events for one specific person, whose \
profile is below. Score each event on how much THEY would want to go - not on \
how famous it is.

PERSON'S PROFILE
{profile}

SCORING
90-100 = they would clear their calendar for this
70-89  = strong match, worth recommending
40-69  = plausible, depends on the week
10-39  = weak match
0-9    = actively wrong for them

Weigh topic fit, vibe, cost sensitivity, travel distance, and time of day \
against the profile. A famous event they'd hate scores low. A small event \
squarely in their interests scores high. If the profile is silent on \
something, score it in the middle rather than guessing.

Return ONLY a JSON array, no prose, no code fence:
[{{"i": <event number>, "score": <integer 0-100>, "reason": "<max 12 words, \
specific to this person>"}}]

EVENTS
{events}"""


class ProviderError(ValueError):
    """An API call failed and the provider said why.

    Subclasses ValueError so llm_scores' existing per-batch handling catches
    it: one bad batch is reported and skipped, not fatal.
    """

    def __init__(
        self,
        message: str,
        status: int | None = None,
        retry_after: float | None = None,
        daily: bool = False,
    ):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        # A per-day quota won't clear by waiting a minute, so retrying it only
        # burns more of tomorrow's allowance.
        self.daily = daily


def _redact(text: str) -> str:
    """Never let an API key reach a log. Gemini puts its key in the URL."""
    return re.sub(r"(key=)[^&\s\"']+", r"\1***", text)


def _post_json(url: str, headers: dict, payload: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        # The status alone ("400 Bad Request") is useless for working out
        # whether the key, the model or the payload is wrong - the body says
        # which, so carry it into the message.
        try:
            body = exc.read().decode("utf-8", "replace").strip()
        except Exception:  # pragma: no cover - body already consumed
            body = ""
        detail = _redact(body)[:400] or exc.reason
        # Gemini's 429 names the exhausted quota near the end of a long body,
        # past where the detail is cut - pull it out so the log says which.
        quota = re.search(r'"quotaId"\s*:\s*"([^"]+)"', body)
        if quota:
            detail = f"quota {quota.group(1)} exhausted"
        raise ProviderError(
            f"HTTP {exc.code}: {detail}",
            status=exc.code,
            retry_after=_retry_after(exc.headers, body),
            daily=bool(quota) and "PerDay" in quota.group(1),
        ) from None


def _retry_after(headers, body: str) -> float | None:
    """How long the provider asked us to wait, if it said.

    Anthropic sends a retry-after header; Gemini puts "retryDelay": "37s" in
    the error body instead.
    """
    value = headers.get("retry-after") if headers else None
    if value:
        try:
            return float(value)
        except ValueError:
            pass
    match = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body or "")
    return float(match.group(1)) if match else None


def _call_anthropic(prompt: str, model: str, api_key: str, timeout: int) -> str:
    data = _post_json(
        ANTHROPIC_URL,
        {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout,
    )
    return "".join(
        block.get("text", "") for block in data.get("content", [])
        if block.get("type") == "text"
    )


def _call_gemini(prompt: str, model: str, api_key: str, timeout: int) -> str:
    # Gemini takes the key as a query parameter rather than a header.
    data = _post_json(
        GEMINI_URL.format(model=model) + f"?key={urllib.parse.quote(api_key)}",
        {},
        {
            "contents": [{"parts": [{"text": prompt}]}],
            # No temperature override: Google warns that going below the
            # default 1.0 on Gemini 3 models can cause looping. Thinking is
            # kept low - scoring against a profile doesn't need deep
            # reasoning, and thinking tokens are billed as output.
            "generationConfig": {"thinkingConfig": {"thinkingLevel": "low"}},
        },
        timeout,
    )
    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError(f"gemini returned no candidates: {str(data)[:200]}")
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts)


# Each entry is (env var holding the key, default model, call function). The
# call signature is (prompt, model, key, timeout) -> reply text, so adding a
# provider is one function plus one line here - nothing else in this module,
# the CLI, or the workflow needs to know which one is in use.
PROVIDERS = {
    "anthropic": ("ANTHROPIC_API_KEY", ANTHROPIC_MODEL, _call_anthropic),
    "gemini": ("GEMINI_API_KEY", GEMINI_MODEL, _call_gemini),
}


def parse_scores(text: str, batch_size: int) -> dict[int, tuple[float, str]]:
    """Pull the JSON array out of a model reply, tolerating fences and prose."""
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        raise ValueError(f"no JSON array in model reply: {text[:200]!r}")
    parsed = json.loads(match.group(0))
    out: dict[int, tuple[float, str]] = {}
    for item in parsed:
        try:
            idx = int(item["i"])
            score = float(item["score"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 1 <= idx <= batch_size:
            continue
        reason = re.sub(r"\s+", " ", str(item.get("reason", ""))).strip()[:120]
        out[idx] = (max(0.0, min(100.0, score)), reason)
    return out


# Waits between retries of a rate-limited batch, when the provider doesn't
# say how long. Free-tier limits are per minute, so the last wait covers a
# full window.
RETRY_WAITS = (15, 30, 65)


def _call_with_retry(call, prompt, model, key, timeout, sleep):
    for wait in RETRY_WAITS + (None,):
        try:
            return call(prompt, model, key, timeout)
        except ProviderError as exc:
            if exc.status not in (429, 503) or exc.daily or wait is None:
                raise
            sleep(min(exc.retry_after or wait, 120))


def llm_scores(
    rows: list[dict],
    profile: str,
    *,
    provider: str = "anthropic",
    model: str | None = None,
    batch_size: int = 20,
    timeout: int = 90,
    min_interval: float = 0,
    on_progress=None,
    sleep=time.sleep,
    clock=time.monotonic,
) -> dict[int, tuple[float, str]]:
    """Score rows against the profile. Returns {event id: (score, reason)}.

    Batches are independent: one failing batch is reported and skipped rather
    than losing the whole run, so a rate limit halfway through still leaves
    you with the batches that succeeded. A 429 is waited out and retried
    first, since the free tiers limit requests per minute. min_interval
    spaces batches at least that many seconds apart, so a run stays under a
    per-minute limit instead of hitting it and waiting.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; expected one of {sorted(PROVIDERS)}")
    env_var, default_model, call = PROVIDERS[provider]
    key = os.environ.get(env_var, "").strip()
    if not key:
        raise RuntimeError(f"{env_var} is not set, so provider {provider!r} can't be used")
    model = model or default_model

    out: dict[int, tuple[float, str]] = {}
    rate_limited = False
    last_call = None
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        if rate_limited:
            # Once retries couldn't get past a 429, this is a daily quota
            # rather than a per-minute one - every later batch would fail the
            # same way. They stay unscored and the next run picks them up.
            if on_progress:
                on_progress(start, len(batch), "skipped (rate limited)")
            continue
        prompt = PROMPT.format(
            profile=profile,
            events="\n".join(_event_line(i, r) for i, r in enumerate(batch, 1)),
        )
        if min_interval and last_call is not None:
            wait = min_interval - (clock() - last_call)
            if wait > 0:
                sleep(wait)
        last_call = clock()
        try:
            reply = _call_with_retry(call, prompt, model, key, timeout, sleep)
            scored = parse_scores(reply, len(batch))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError) as exc:
            if getattr(exc, "status", None) == 429:
                rate_limited = True
            if on_progress:
                on_progress(start, len(batch), f"FAILED ({type(exc).__name__}: {exc})")
            continue
        for idx, value in scored.items():
            out[batch[idx - 1]["id"]] = value
        if on_progress:
            on_progress(start, len(batch), f"{len(scored)} scored")
    return out
