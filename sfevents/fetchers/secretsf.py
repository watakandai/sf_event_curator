"""Secret San Francisco (secretsanfrancisco.com): editorial event picks.

Unlike every other source, this one publishes news articles, not listings:
the date, time and venue live in prose ("returning this Friday, September
25 ... from 5 to 10 pm on Irving Street"). So the fetch is two steps:

1. Pull recent "Things To Do" posts from the site's public WordPress REST
   API - clean JSON, no HTML scraping.
2. Ask an LLM to turn each article into zero or more structured events.
   Groq goes first so this doesn't eat into the small Gemini quota the
   ranker depends on; see DEFAULT_PROVIDERS.

Extractions are cached in the database by (article id, last-modified), so
an article costs one model call when it first appears and again only if the
site edits it. With every provider down, previously extracted articles still
come back from the cache and new ones wait for the next run.
"""
from __future__ import annotations
import html
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("America/Los_Angeles")
except Exception:  # pragma: no cover - no tz database
    LOCAL_TZ = None

from .. import db
from ..llm import FallbackLLM, NoProviderAvailable
from ..models import Event
from .tribe import collapse_repeats

API_URL = "https://secretsanfrancisco.com/wp-json/wp/v2/posts"
THINGS_TO_DO = 13  # English "Things To Do"; the Spanish copies are 740
SPONSORED = 12
LOOKBACK_DAYS = 21
# Groq first: its free tier allows ~1K requests a day, where Gemini's allows
# ~20 and the ranker needs them. SECRETSF_PROVIDERS overrides.
DEFAULT_PROVIDERS = ("groq", "gemini", "ollama")
# Seconds between calls to one provider. An article prompt is up to ~1.6K
# tokens plus the reply, and Groq's free tier caps tokens per minute at 8K.
PACING = {"groq": 20.0, "gemini": 15.0}
MAX_ARTICLE_CHARS = 6000
MAX_EVENTS_PER_ARTICLE = 10

# One per event, so the page's kind chips pick it up (docs/index.html KINDS).
KINDS = (
    "festival", "market", "film", "theater & performance", "art & museums",
    "comedy", "live music", "food & drink", "community", "kids & families",
    "outdoors", "sports",
)

PROMPT = """You turn a San Francisco news article into event listings.

The article was published {published} (Pacific time). Resolve relative \
dates like "this Friday" or "next weekend" against that date.

Return ONLY a JSON object, no prose, no code fence:
{{"events": [{{"title": "<the event's own name>", \
"start": "<YYYY-MM-DD, or YYYY-MM-DDTHH:MM if a start time is given>", \
"end": "<same format, or null>", "venue": "<place name or empty>", \
"address": "<street and city, or empty>", \
"cost": "<\\"0\\" if free, a price like \\"$25\\" or \\"$15-40\\", or empty if unstated>", \
"kind": "<one of: {kinds}>", \
"summary": "<one sentence on what it is>"}}]}}

Rules:
- Only things a person can attend in person on a specific date or date \
range. A multi-week exhibit or pop-up is one entry with start and end.
- One event on several separate dates: one entry per date, at most 6.
- A round-up of many events: at most {max_events}, the most prominent.
- Not an event (news, a guide, a restaurant review, a contest or \
giveaway, a list with no dates): {{"events": []}}.
- Never guess. No stated time means a date-only start. No venue means "".

ARTICLE TITLE
{title}

ARTICLE
{body}"""


class SecretSFFetcher:
    name = "secretsf"

    def __init__(self, api_url: str = API_URL, timeout: int = 20,
                 lookback_days: int = LOOKBACK_DAYS, providers=None, llm=None):
        self.api_url = api_url
        self.timeout = timeout
        self.lookback_days = lookback_days
        self.providers = providers
        self.llm = llm
        self.db_path = None
        self.report = ""
        # Article ids whose events this run fully knows (cached or freshly
        # extracted) - the CLI prunes their stale rows, and nothing else.
        self.covered_articles: list[str] = []

    def bind_db(self, db_path) -> None:
        """Where extractions are cached. Without one, every run re-extracts."""
        self.db_path = db_path

    def fetch(self) -> list[Event]:
        after = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        query = urllib.parse.urlencode({
            "categories": THINGS_TO_DO,
            "categories_exclude": SPONSORED,
            "after": after.strftime("%Y-%m-%dT%H:%M:%S"),
            "per_page": 50,
            "_embed": "wp:featuredmedia",
            "_fields": "id,date,modified,link,title,content,excerpt,_links,_embedded",
        })
        req = urllib.request.Request(
            f"{self.api_url}?{query}", headers={"User-Agent": "sfevents/0.1"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            posts = json.loads(resp.read().decode())
        return self.parse(posts)

    def parse(self, posts: list[dict]) -> list[Event]:
        llm = self.llm or FallbackLLM(
            list(self.providers or _providers_from_env()), min_interval=PACING
        )
        events: list[Event] = []
        self.covered_articles = []
        cached = extracted = failed = 0
        last_error = ""
        for post in posts:
            article_id = str(post.get("id", ""))
            modified = post.get("modified") or post.get("date") or ""
            if not article_id:
                continue
            result = None
            if self.db_path:
                result = db.get_extraction(self.db_path, self.name, article_id, modified)
            if result is not None:
                cached += 1
            else:
                if not llm.available:
                    failed += 1
                    continue
                try:
                    result, by = llm.complete(_prompt(post), parse=parse_reply)
                except NoProviderAvailable as exc:
                    # Includes replies that never parsed. Not cached, so the
                    # next run gets another go at the article.
                    failed += 1
                    last_error = str(exc)
                    continue
                extracted += 1
                if self.db_path:
                    db.put_extraction(self.db_path, self.name, article_id, modified, result, by)
            # A show with six showtimes is one card with "Repeats: 5 more
            # dates", as for the other sources - not six cards.
            events.extend(collapse_repeats(to_events(post, result, self.name)))
            self.covered_articles.append(article_id)

        self.report = f"{len(posts)} articles: {cached} cached, {extracted} extracted"
        if failed:
            self.report += f", {failed} not extracted (retried next run)"
            if llm.unconfigured and not llm.providers:
                self.report += f" - no LLM key set for {', '.join(llm.unconfigured)}"
            elif last_error:
                self.report += f" - last error: {last_error[:200]}"
        return events


def _providers_from_env() -> tuple[str, ...]:
    raw = os.environ.get("SECRETSF_PROVIDERS", "")
    names = tuple(p.strip() for p in raw.split(",") if p.strip())
    return names or DEFAULT_PROVIDERS


def _prompt(post: dict) -> str:
    return PROMPT.format(
        published=(post.get("date") or "")[:10],
        kinds=", ".join(KINDS),
        max_events=MAX_EVENTS_PER_ARTICLE,
        title=_clean(post.get("title", {}).get("rendered", "")),
        body=article_text(post.get("content", {}).get("rendered", ""))[:MAX_ARTICLE_CHARS],
    )


def article_text(fragment: str) -> str:
    """Article HTML to plain text, one paragraph per line."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", fragment or "", flags=re.S | re.I)
    text = re.sub(r"</?(br|p|li|h\d|div|blockquote|figure|figcaption)\b[^>]*>", "\n",
                  text, flags=re.I)
    # A tag cut off by truncation would otherwise leak its attributes in.
    text = html.unescape(re.sub(r"<[^>]*$", "", re.sub(r"<[^>]+>", "", text)))
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    # Embedded-post chrome and the related-links footer only cost tokens.
    lines = [l for l in lines if l and l != "View this post on Instagram"]
    for i, line in enumerate(lines):
        if line.lower().startswith("see also"):
            lines = lines[:i]
            break
    return "\n".join(lines)


def parse_reply(text: str) -> list[dict]:
    """The events list out of a model reply, tolerating fences and prose."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError(f"no JSON object in reply: {(text or '')[:120]!r}")
    data = json.loads(match.group(0))
    items = data.get("events") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("reply has no events list")
    return [item for item in items if isinstance(item, dict)][:MAX_EVENTS_PER_ARTICLE]


def to_events(post: dict, items: list[dict], source: str) -> list[Event]:
    """Validated Events from one article's extraction.

    The model's output is checked rather than trusted: an entry without a
    title or a parseable date is dropped, and so is one dated before the
    article or more than a year after it - a sign it misread the year.
    """
    published = _published(post)
    link = post.get("link", "")
    images = _images(post)
    excerpt = article_text(post.get("excerpt", {}).get("rendered", ""))
    events: list[Event] = []
    seen: set[str] = set()
    for item in items:
        title = _clean(item.get("title"))
        start = _when(item.get("start"))
        if not title or start is None:
            continue
        start_day = start.date()
        if published and not (published - timedelta(days=2) <= start_day
                              <= published + timedelta(days=400)):
            continue
        end = _when(item.get("end"))
        if end is not None and end.replace(tzinfo=None) < start.replace(tzinfo=None):
            end = None
        source_id = f"{post.get('id')}:{start_day.isoformat()}"
        n = 2
        while source_id in seen:
            source_id = f"{post.get('id')}:{start_day.isoformat()}:{n}"
            n += 1
        seen.add(source_id)
        kind = _clean(item.get("kind")).lower()
        events.append(Event(
            source=source,
            source_id=source_id,
            title=title,
            start=start,
            end=end,
            venue=_clean(item.get("venue")),
            address=_clean(item.get("address")),
            cost=_cost(item.get("cost")),
            categories=[kind] if kind in KINDS else [],
            url=link,
            description=_clean(item.get("summary")) or excerpt[:300],
            images=images,
        ))
    return events


def _when(value) -> datetime | None:
    """ISO date or date-time from the model to the codebase's convention.

    Date-only means all-day: a naive midnight, as the other sources store
    all-day events. A time is Pacific wall-clock time, so it gets the zone.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        if len(value) <= 10:
            return datetime.combine(date.fromisoformat(value), datetime.min.time())
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None and LOCAL_TZ is not None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed


def _published(post: dict) -> date | None:
    try:
        return datetime.fromisoformat(post.get("date", "")).date()
    except ValueError:
        return None


def _cost(value) -> str:
    cost = _clean(value)
    return "0" if cost.lower() in {"0", "free", "$0"} else cost


def _images(post: dict) -> list[str]:
    media = (post.get("_embedded") or {}).get("wp:featuredmedia") or []
    url = media[0].get("source_url") if media and isinstance(media[0], dict) else ""
    return [url] if url else []


def _clean(value) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()
