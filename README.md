# sfevents

Fetches Bay Area events into a local SQLite store, ranks them against a
profile you write, and browses them as a list or a calendar - with a
read-only static export for GitHub Pages.

## Features

- **Ten sources** (~1,300 upcoming events): curated annual-events and
  seasons lists plus [SF Funcheap](https://sf.funcheap.com),
  [Secret San Francisco](https://secretsanfrancisco.com)'s editorial picks,
  [DoTheBay](https://dothebay.com), [SF Rec & Parks](https://sfrecpark.org),
  [19hz](https://19hz.info), and the Santa Cruz, Sausalito and Bodega Bay
  visitor calendars for day trips
- **In season**: whale watching, elephant seals, monarchs, pumpkin patches,
  u-pick fruit, the grape crush, urchin diving, mushrooms, crab season and
  more within ~3 hours of SF, drawn as colored bars running across the
  calendar weeks (Google Calendar style) and described in a strip under the
  list; click a bar for its card. Seasons stay out of the ranked list, where a
  months-long season would crowd out real events
- **Curated annual events** so the big Bay Area dates (Outside Lands, Pride,
  Folsom, Mill Valley Fall Arts) are on your radar months ahead, not the week
  of - stored as recurrence rules, not dates, so the list doesn't rot
- **Ranking**: a free offline heuristic always runs; optionally an LLM scores
  every event against `profile.md` - your background and taste in plain
  English. Pluggable provider: Claude, Gemini, Groq or a local Ollama model,
  with fallbacks
- **Calendar home page**: click a day and the list under the grid ranks
  that **Day**, the **Week**, or the **Month** from it (today through the
  same date next month by default), best match first, 5 per page for up to
  4 pages. The range is highlighted on the grid and the choice is remembered
- **A persistent map beside the results**, Yelp-style: it follows the top
  current page of the list, and selection is two-way - click a card
  to spotlight its pin, click a pin to highlight and scroll to its card
- **Filter by kind**: toggle chips for In Season, Festivals, Arts & Film, Community &
  Food, Outdoors & Sports, Parks & Rec, Comedy & Shows and Music & Nightlife.
  Music starts switched off (it's about two thirds of the corpus) - see
  Filtering
- **Event artwork, times and mapped locations**: cover images where a source
  publishes them, start/end times, addresses linked to Google Maps, and a
  per-day map view in the calendar (Leaflet + OpenStreetMap)
- SQLite storage with dedup on `(source, source_id)`, so repeated fetches
  never create duplicates
- Web dashboard: browse, filter (free-only, category), and manually
  add/edit/delete events (e.g. "Ocean Beach bonfire, Friday, bring firewood")
- Static JSON export for a read-only deployment (GitHub Pages)
- 202 tests, no network required to run them

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python3 -m pytest -q

python3 -m sfevents.cli fetch          # pull events into the store
python3 -m uvicorn sfevents.web:app --reload   # dashboard at http://127.0.0.1:8000
```

## CLI

```bash
python3 -m sfevents.cli fetch                   # pull from all sources
python3 -m sfevents.cli geocode                 # venue text -> coordinates (cached)
python3 -m sfevents.cli rank                    # heuristic scores, offline & free
python3 -m sfevents.cli rank --llm              # also score against profile.md
python3 -m sfevents.cli list --sort score       # best match first
python3 -m sfevents.cli export --out docs/data/events.json --sort score
```

`--db path` overrides the default DB location (`~/.sfevents/events.db`).

`export` drops past events and trims bookkeeping columns by default, since the
static page is fetched on every visit; `--include-past` and `--full` opt out.

## Maps and images

`geocode` resolves venue text to coordinates so the calendar's day view can
show a map. It is keyed by **place, not event** - about 350 distinct places
cover ~1,300 events - and cached in the database permanently, so the first
run does real work and later runs do almost none. The exported JSON carries
the coordinates, so the page itself geocodes nothing.

It uses Nominatim (OpenStreetMap): no API key, but it asks for at most one
request per second and an identifying User-Agent, both of which this honours.
`--limit` caps requests per run so a backlog is worked off over several runs.
Failures are cached too, so an unparseable venue isn't re-queried weekly. At
real scale, use a paid geocoder rather than leaning on a donated service.

Results are validated against a Northern California bounding box. A viewbox
alone is only a *preference*, and generic venue names genuinely resolved to
the wrong half of the state ("Union Square Plaza" landed near Santa Clarita);
a confident wrong pin is worse than no pin. About 78% of events get
coordinates; the rest are venues like "TBA" or unnumbered cross-streets.

**Images** come only from sources that publish them per event: DoTheBay cover
art (reliable) and Funcheap RSS enclosures (some point at uploads its CDN no
longer serves, so the UI drops an image that fails to load). Roughly a
quarter of events have artwork. No source currently provides more than one
image per event, so the carousel controls are present but dormant - see the
note in Roadmap.

## Filtering by kind

Every source tags events its own way - Funcheap says "Fairs & Festivals",
DoTheBay "Theater & Performance", 19hz a genre name - so the raw categories
are too many and too uneven to filter on. The page folds them into a handful
of kinds (the `KINDS` table in the page's script) and shows them as toggle
chips, each with a count. An event can belong to several kinds, and it shows
if any of them is switched on.

Music & Nightlife starts switched off: 19hz is an electronic-music listing end
to end and DoTheBay skews the same way, so music is roughly two thirds of
everything fetched. Because kinds overlap, the big festivals need no special
case - Outside Lands and Portola are also Festivals, Stern Grove and Hardly
Strictly are also Outdoors, so they stay visible with Music off while a club
night doesn't. Your chip choices are remembered in the browser.

It's a filter, not a dropped source: music events are still fetched, ranked
and one click away, and they still feed the ranker's cross-source signal.

DoTheBay categories come from each listing card's own
`ds-event-category-*` class, which is what makes filtering by kind of event
possible for that source at all.

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
export GEMINI_API_KEY=...          # or ANTHROPIC_API_KEY
python3 -m sfevents.cli rank --llm --provider gemini
```

| Provider | Env var | Default model | Notes |
|---|---|---|---|
| `gemini` | `GEMINI_API_KEY` | `gemini-3.6-flash` | Cheapest option |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5` | `--model claude-haiku-4-5-20251001` is cheaper |
| `groq` | `GROQ_API_KEY` | `openai/gpt-oss-120b` | Free tier, no card; capped at 8K tokens/minute |
| `ollama` | `OLLAMA_HOST` (server URL) | `qwen3.5:4b` | Free and local, no quota; slow on a CPU. `OLLAMA_MODEL` overrides |

`--fallback groq` hands whatever the main provider leaves unscored - Gemini's
"high demand" 503s, a spent quota - to the next provider in the list, in its
own smaller batches with a pause between them (`FALLBACK_PACING` in
`rank.py`). Each score records the model that actually gave it.

GitHub Models used to be a third, free option; GitHub retired the service on
July 30, 2026, so it was removed.

Adding a provider is one function plus one line in `PROVIDERS` (`rank.py`).

Scores are cached by a hash of `profile.md` + model, so a weekly run only pays
to score events it has never seen. **Editing `profile.md` re-ranks everything**
on the next run - that's the point, but it isn't free.

## Bay Area Adventure Club

A way to invite friends without asking anyone directly. You pitch events in
`docs/plans.json`, and friends tap **I'm in** on the site. Each pitch stays
**Tentative** until `min` friends are in. Then it's **on**, and you make the
Partiful. If the `deadline` passes before that, it shows as "didn't tip" and
drops off once its date has passed.

```json
{
  "club": "Bay Area Adventure Club",
  "host": "Kandai",
  "rsvp_url": "https://script.google.com/macros/s/.../exec",
  "plans": [
    { "event": "seasonal_bay_area:gray-whales-point-reyes:2026-12-15", "date": "2027-01-09",
      "time": "09:30", "min": 3, "deadline": "2027-01-06", "note": "Carpool from the Mission" },
    { "event": "annual_bay_area:mill-valley-film-festival:2026-10-08", "min": 2, "deadline": "2026-10-14",
      "partiful": "https://partiful.com/e/..." },
    { "title": "Bonfire at Ocean Beach", "date": "2026-10-03", "time": "18:00", "until": "21:00",
      "venue": "Ocean Beach", "min": 4, "deadline": "2026-09-30" }
  ]
}
```

- `event` is the event's `key` (`source:source_id`) from `docs/data/events.json`.
  Leave it out and give `title`, `date` and `venue` for something no feed lists.
- `date`, `time` and `until` pin a day and time. A season needs this, since it
  runs for months.
- `min` is how many friends must say they're in. It defaults to 3, and 0 means
  the plan is on regardless.
- `deadline` is the last day to say you're in.
- `partiful` is the Partiful link, once you've made it. Friends get a
  **Copy Partiful link** button, on the plan and on the event's card.

A plan's RSVPs are tied to its id: `event` plus `@date`, or a slug of the
`title`. Changing either starts the count over. Set `"id"` to keep a fixed id.

Pushing `docs/plans.json` publishes it straight away, with no workflow run
needed. Only counts are shown on the site; who said yes stays in the sheet.

**Host mode.** Open the site with `?host=1` (and `?host=0` to turn it off) to
get these buttons:

- **Send update to WhatsApp** opens WhatsApp with this week's pitches,
  counts and deadlines already written. Pick the "Bay Area Adventure Club" group
  and press send. WhatsApp has no API for posting to a personal group, so that
  last tap is yours.
- **Create Partiful**, on a plan that's on, copies its details and opens
  partiful.com/create. Partiful has no API either.

### Setting up the RSVP sheet (once, about 5 minutes)

1. Create a Google Sheet and open **Extensions → Apps Script**.
2. Replace `Code.gs` with [`apps_script/Code.gs`](apps_script/Code.gs) and save.
3. Click **Deploy → New deployment**, choose type **Web app**, set
   *Execute as* to **Me** and *Who has access* to **Anyone**, then Deploy.
   Authorize it when asked. It needs the sheet, fetching the site, and sending
   mail to you.
4. Copy the web app URL (ending in `/exec`) into `rsvp_url` in
   `docs/plans.json` and push.

When a pitch reaches its minimum, the script emails the sheet's owner once.
After editing `Code.gs`, deploy again: **Manage deployments → Edit → New
version**, which keeps the same URL.

The endpoint is public, like the site, so anyone who finds it could add a
fake RSVP. That's fine among friends, and the sheet shows every row if
something looks off.

## Layout

```
sfevents/
  models.py           Event dataclass
  db.py                SQLite schema, CRUD, query filters
  rank.py              heuristic + LLM rankers, provider adapters
  geocode.py           venue text -> coordinates, via Nominatim
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
docs/plans.json         Adventure Club pitches, hand-edited
apps_script/Code.gs     RSVP backend, pasted into a Google Sheet's Apps Script
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

**Secret San Francisco** — editor-picked events, published as news
articles rather than listings, so the dates and venues are in prose. The
fetcher pulls the last 3 weeks of "Things To Do" posts from the site's public
WordPress API (Sponsored posts excluded) and has an LLM turn each article
into zero or more events: Groq first, then Gemini, so extraction doesn't
spend the small Gemini quota the ranker needs (`SECRETSF_PROVIDERS`
overrides the order). The model's answer is checked, not trusted: an entry
dated before the article or over a year after it is dropped. Each article
is extracted once and cached in the database by its last-edit time, so a
week costs about 10 model calls; with no key set, the source just adds
nothing new.

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

**Curated seasons** (`data/seasonal.json`) — also not a network fetch.
Things you can do on any day inside a window of weeks or months, which no
event feed lists: whale migrations, pupping and spawning seasons, harvests
and u-pick, crab and urchin seasons, snow. Each entry is a `from`/`to`
month-day pair (it may wrap the new year), and each year's window becomes one
event with a start and end. All are typical timing and marked approximate
unless the entry says otherwise. Descriptions carry the drive from SF and any
reservation or license you need. The dated harvest-time festivals (grape
stomp, pumpkin weigh-off, crane and whale festivals) live in the annual list.

**Visitor-bureau calendars** (`fetchers/tribe.py`) — many towns' tourism sites
run the same WordPress plugin, The Events Calendar, whose REST API
(`/wp-json/tribe/events/v1/events`) is public and identical everywhere. One
fetcher covers them all: Santa Cruz (state-park tours, the monarch walks,
Elkhorn Slough, festivals), Sausalito and Bodega Bay, 60 days ahead. These
calendars repeat daily tours dozens of times, so only the next date of each
title is kept, with a "Repeats: N more dates" note. To add a town, check
`https://<site>/wp-json/tribe/events/v1/events` returns JSON and add a
`TribeEventsFetcher` line to `FETCHERS`. South Lake Tahoe works too but was
left out: ~800 listings, mostly bar nights, 3.5h+ away.

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

**Self-hosted (systemd + cron)** — `deploy/sfevents.service` runs
the dashboard persistently on `127.0.0.1`; `deploy/crontab.txt` +
`scripts/weekly_fetch.sh` handle the weekly fetch. No auth on the dashboard,
so don't expose it beyond localhost/your LAN without a reverse proxy.

**Oracle Cloud (or any VPS)** — same systemd service, plus a reverse proxy
in front since it's now internet-facing. `deploy/nginx-sfevents.conf`
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
`gemini` or `anthropic` (Settings → Variables) and add the
matching secret. Left unset, the LLM step is skipped and the site ships
heuristic scores only, so a fork with no keys still works. `RANKER_LIMIT`
caps how many events get sent per run; a manual run can override it, e.g. to
score the whole backlog at once:

```bash
gh workflow run "Weekly event fetch" -f limit=0
```

Add `-f rescore_all=true` to re-score events that already have an LLM score.
Gemini's free tier (a key from a project with billing off) is enough for
this, but it caps *requests* per day (20 at the time of writing), not events.
So CI sends 50 events per request (`RANKER_BATCH_SIZE`) and leaves 15 seconds
between requests (`RANKER_MIN_INTERVAL`) to stay under the per-minute limit.
If a per-minute limit is hit anyway, the ranker waits and retries; once the
daily quota is used up it stops and leaves the rest for the next run. Runs
are queued, never overlapped, so two runs can't split one quota or lose each
other's scores.

Events Gemini doesn't score go to Groq, then to Ollama (`RANKER_FALLBACK`,
default `groq,ollama`). Groq needs the `GROQ_API_KEY` secret. Ollama needs
nothing: the workflow installs it and pulls `qwen3.5:4b` (`OLLAMA_MODEL`) on
the runner in the background while events are fetched, and it only gets the
events both hosted providers missed. A 4B model on the runner's CPU takes
roughly a minute per 10 events, and scores less sharply than a hosted model,
so it's the last resort rather than the default. **A run that still leaves events
unscored goes red**: `rank --llm` exits non-zero, the site ships anyway with
heuristic scores for the rest, and the failing "Alert" step lists the failed
batches on the run page. GitHub emails a failed scheduled run to whoever last
edited the cron line.

The workflow **caches the SQLite database between runs** — that's what makes
LLM ranking affordable, since only unseen events are scored. Losing the cache
is harmless; the next run re-scores everything once.

## Roadmap

- Semantic search (ChromaDB) once there's enough data to search over
- More than one image per event, which is what the carousel was built for.
  Would mean fetching each event's own page for its gallery or og:image -
  hundreds of requests per run against third-party ticketing sites, so it
  needs rate limiting and caching before it's reasonable
- Feed the ranker actual outcomes (what you went to) instead of only a
  written profile — the obvious next step, and the one that would let the
  ranking improve on its own
- Partiful draft generation for events worth creating
- Dashboard authentication

