# sf_event_curator

Fetches SF events into a local SQLite store and browses/filters them from a
web dashboard, with a read-only static export for GitHub Pages.

## Features

- Fetchers for [SF Funcheap](https://sf.funcheap.com) (RSS) and
  [DoTheBay](https://dothebay.com) (HTML scrape)
- SQLite storage with dedup on `(source, source_id)`, so repeated fetches
  never create duplicates
- Web dashboard: browse, filter (free-only, category, date range), and
  manually add/edit/delete events (e.g. "Ocean Beach bonfire, Friday, bring
  firewood")
- Static JSON export for a read-only deployment (GitHub Pages)
- 77 tests, no network required to run them

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
python3 -m sf_event_curator.cli fetch                        # pull from all sources
python3 -m sf_event_curator.cli list                         # print stored events
python3 -m sf_event_curator.cli export --out docs/data/events.json  # static-site export
```

`--db path` overrides the default DB location (`~/.sf_event_curator/events.db`).

## Layout

```
sf_event_curator/
  models.py           Event dataclass
  db.py                SQLite schema, CRUD, query filters
  fetchers/
    base.py            Fetcher protocol
    funcheap.py         RSS fetcher
    dothebay.py          HTML scraper (see Sources below)
  cli.py               fetch / list / export commands
  web.py               FastAPI app + REST API
  static/index.html    Dashboard frontend
docs/index.html         Read-only static frontend (GitHub Pages)
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

**Not supported:** Eventbrite (public search API was discontinued in 2020;
only returns events you already own) and Secret San Francisco (an editorial
blog, not a structured event source).

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

**GitHub Pages (read-only)** — `.github/workflows/weekly_fetch.yml` fetches
and exports to `docs/data/events.json` on a schedule; Pages serves `docs/`
directly. No backend, so no add/edit/delete — this is the free,
zero-maintenance option when a shared read/write dashboard isn't needed.
Setup: enable Pages (source: `main` branch, `/docs` folder) and give Actions
"read and write" permission (Settings → Actions → General) so the workflow
can commit the export.

## Roadmap

- Semantic search (ChromaDB) once there's enough data to search over
- Curation/recommendation logic
- Partiful draft generation for events worth creating
- Dashboard authentication

