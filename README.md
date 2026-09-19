# sf_event_curator

Fetches Bay Area events into a local SQLite store, ranks them against a
profile you write, and browses them as a list or a calendar - with a
read-only static export for GitHub Pages.

## Features

- **Five sources** (~1,000 events): a curated annual-events list plus
  [SF Funcheap](https://sf.funcheap.com), [DoTheBay](https://dothebay.com),
  [SF Rec & Parks](https://sfrecpark.org), and [19hz](https://19hz.info)
- **Curated annual events** so the big Bay Area dates (Outside Lands, Pride,
  Folsom, Mill Valley Fall Arts) are on your radar months ahead, not the week
  of - stored as recurrence rules, not dates, so the list doesn't rot
- **Ranking**: a free offline heuristic always runs; optionally an LLM scores
  every event against `profile.md` - your background and taste in plain
  English. Pluggable provider: Claude, Gemini, or GitHub Models' free tier
- **List and calendar views**, sortable by best match or by date
- SQLite storage with dedup on `(source, source_id)`, so repeated fetches
  never create duplicates
- Web dashboard: browse, filter (free-only, category), and manually
  add/edit/delete events (e.g. "Ocean Beach bonfire, Friday, bring firewood")
- Static JSON export for a read-only deployment (GitHub Pages)
- 155 tests, no network required to run them

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python3 -m pytest -q

python3 -m sf_event_curator.cli fetch          # pull events into the store
python3 -m uvicorn sf_event_curator.web:app --reload   # dashboard at http://127.0.0.1:8000
```

## CLI

```bash
python3 -m sf_event_curator.cli fetch                   # pull from all sources
python3 -m sf_event_curator.cli rank                    # heuristic scores, offline & free
python3 -m sf_event_curator.cli rank --llm              # also score against profile.md
python3 -m sf_event_curator.cli list --sort score       # best match first
python3 -m sf_event_curator.cli export --out docs/data/events.json --sort score
```

`--db path` overrides the default DB location (`~/.sf_event_curator/events.db`).

`export` drops past events and trims bookkeeping columns by default, since the
static page is fetched on every visit; `--include-past` and `--full` opt out.

## Ranking

Two rankers answer two different questions.

**The heuristic** (`rank`, no flags) asks *how big a deal is this generally?*
It is deterministic, offline and free, so it always runs and is the floor when
there's no API key. Signals: the curated notability prior, cross-source
agreement, marquee event words, free-ness, data completeness. It deliberately
can't tell a great club night from a dull one - most scraped events land in a
narrow band.

**The LLM ranker** (`rank --llm`) asks the useful question: *would you want to
go?* It reads `profile.md` - plain English, no schema - and scores each event
0-100 with a one-line reason shown on the card.

```bash
cp profile.example.md profile.md   # then edit it; the default is a guess
export GEMINI_API_KEY=...          # or ANTHROPIC_API_KEY, or GITHUB_TOKEN
python3 -m sf_event_curator.cli rank --llm --provider gemini
```

| Provider | Env var | Default model | Notes |
|---|---|---|---|
| `gemini` | `GEMINI_API_KEY` | `gemini-2.5-flash` | Cheapest paid option |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-haiku-4-5-20251001` | |
| `github-models` | `GITHUB_TOKEN` | `openai/gpt-4o-mini` | Free tier, rate-limited |

Adding a provider is one function plus one line in `PROVIDERS` (`rank.py`).

Scores are cached by a hash of `profile.md` + model, so a weekly run only pays
to score events it has never seen. **Editing `profile.md` re-ranks everything**
on the next run - that's the point, but it isn't free.

## Layout

```
sf_event_curator/
  models.py           Event dataclass
  db.py                SQLite schema, CRUD, query filters
  rank.py              heuristic + LLM rankers, provider adapters
  data/
    annual_events.json Curated annual events, as recurrence rules
  fetchers/
    base.py            Fetcher protocol
    annual.py           Materializes annual_events.json into dated events
    funcheap.py         RSS fetcher
    dothebay.py          HTML scraper (see Sources below)
    sfrecpark.py         schema.org microdata parser
    nineteenhz.py        HTML table parser
  cli.py               fetch / rank / list / export commands
  web.py               FastAPI app + REST API
  static/index.html    Dashboard frontend
docs/index.html         Read-only static frontend (GitHub Pages)
profile.md              Your background and taste, read by the LLM ranker
profile.example.md      Template for the above
deploy/                 systemd unit, nginx config, crontab example
scripts/weekly_fetch.sh Cron wrapper for the fetch job
.github/workflows/       Scheduled fetch + export, for the GitHub Pages deployment
tests/
```

## Sources

**SF Funcheap** — RSS feed with a custom namespace (start/end time, cost,
venue, categories). No API key needed, and the richest structured source
available.

**DoTheBay** — has no public RSS or API, so this scrapes server-rendered
HTML instead, matching by anchor URL pattern (`/events/YYYY/M/D/slug`,
`/venues/slug`) rather than CSS classes, since routing is more stable than
markup. Known limitations: no time-of-day, no cost/category data. Treated
as a fragile source in `cli.py` — a failure or a drop to 0 results never
blocks other fetchers, and prints a warning.

**Curated annual events** (`data/annual_events.json`) — not a network fetch.
The scraped sources only know about the next week or two, so nothing in them
tells you in March that Outside Lands is in August. Entries store recurrence
*rules* ("last Sunday of September", "third weekend of September"), so the
file doesn't need an annual edit. Where timing is only typical - anything
following a lunar calendar, a league schedule, or an organizer's whim - the
entry is marked `approx` and the UI flags it with `~` and a "date approx"
badge. Don't book travel on one without confirming.

**SF Rec & Parks** — the calendar page is server-rendered ASP.NET carrying
schema.org/Event microdata, so this reads `itemprop` names (a vocabulary the
site must keep stable for search engines) rather than CSS classes. Covers the
free, daytime, all-ages side of the city. Real start times and full street
addresses; no cost data, so `cost` is left empty rather than guessed.

**19hz** — hand-maintained Bay Area electronic/club listing, several hundred
events deep and months ahead. The only source carrying both a real start time
and a ticket price. Fragile: the page's markup is invalid in places (title
cells are left unclosed), so parsing is anchored to column order and the
hidden sortable-date column. Note its coverage spills past the Bay proper
into Santa Cruz, Sacramento and beyond.

**Not supported:** Eventbrite (public search API was discontinued in 2020;
only returns events you already own), Secret San Francisco (an editorial
blog, not a structured event source), and Funcheap's regional editions
(`eastbay.funcheap.com` and friends don't resolve; the SF feed is also capped
at 10 items with no pagination).

## Adding a source

Implement `Fetcher` (`name: str`, `fetch() -> list[Event]`) in `fetchers/`,
add an instance to `FETCHERS` in `cli.py`. If it's scraped rather than
RSS/API-based, add its name to `FRAGILE_SOURCES` too.

## Deployment

Three options, same core app:

**Self-hosted (systemd + cron)** — `deploy/sf-event-curator.service` runs
the dashboard persistently on `127.0.0.1`; `deploy/crontab.txt` +
`scripts/weekly_fetch.sh` handle the weekly fetch. No auth on the dashboard,
so don't expose it beyond localhost/your LAN without a reverse proxy.

**Oracle Cloud (or any VPS)** — same systemd service, plus a reverse proxy
in front since it's now internet-facing. `deploy/nginx-sf-event-curator.conf`
adds basic auth; `deploy/oracle_cloud_setup.md` covers OCI-specific
networking (Security List + the instance's own iptables rules both need
opening) and an alternative Caddy-based setup with automatic HTTPS. Common
gotcha on OCI specifically: Always Free Ampere instances often report "out
of host capacity" — `VM.Standard.E2.1.Micro` is a reliable fallback.

**GitHub Pages (read-only)** — `.github/workflows/weekly_fetch.yml` fetches,
ranks and exports to `docs/data/events.json` on a schedule; Pages serves
`docs/` directly. No backend, so no add/edit/delete — this is the free,
zero-maintenance option when a shared read/write dashboard isn't needed.

Setup: enable Pages (source: `main` branch, `/docs` folder) and give Actions
"read and write" permission (Settings → Actions → General) so the workflow
can commit the export.

To turn on LLM ranking in CI, set the repo variable `RANKER_PROVIDER` to
`gemini`, `anthropic` or `github-models` (Settings → Variables) and add the
matching secret. Left unset, the LLM step is skipped and the site ships
heuristic scores only, so a fork with no keys still works. `RANKER_LIMIT`
caps how many events get sent per run.

The workflow **caches the SQLite database between runs** — that's what makes
LLM ranking affordable, since only unseen events are scored. Losing the cache
is harmless; the next run re-scores everything once.

## Roadmap

- Semantic search (ChromaDB) once there's enough data to search over
- Feed the ranker actual outcomes (what you went to) instead of only a
  written profile — the obvious next step, and the one that would let the
  ranking improve on its own
- Partiful draft generation for events worth creating
- Dashboard authentication

