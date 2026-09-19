# sf_event_curator

An SF event curation system: fetch events into a local SQLite store,
browse/filter/add/edit/delete them from a web dashboard. The fetch/store/CLI
layer is stdlib-only; the dashboard adds FastAPI + uvicorn.

## Layout

```
sf_event_curator/
  models.py           Event dataclass
  db.py                SQLite schema, upsert (dedupes on source+source_id),
                        queries, get/update/insert_manual_event for the dashboard
  fetchers/
    base.py            Fetcher protocol - implement .fetch() -> list[Event]
    funcheap.py         SF Funcheap RSS fetcher (free/cheap events, structured feed)
    dothebay.py          DoTheBay HTML scraper (FRAGILE - no RSS/API exists, see below)
  cli.py               `fetch` and `list` commands
  web.py               FastAPI app: REST API + serves the dashboard
  static/index.html    Dashboard frontend (vanilla JS, no build step)
deploy/
  sf-event-curator.service   systemd unit for the dashboard (persistent, auto-restart)
  crontab.txt                  example weekly-fetch crontab entry
scripts/
  weekly_fetch.sh      Cron wrapper: runs `cli fetch`, logs each run to ~/.sf_event_curator/fetch.log
tests/
  fixtures/funcheap_sample.xml    real feed sample, captured 2026-09-05
  fixtures/dothebay_sample.html   HAND-BUILT fixture (not a captured response - see dothebay.py docstring)
  test_models.py                  Event dataclass edge cases (is_free, field isolation)
  test_funcheap_fetcher.py       parsing: fixture, malformed dates, missing fields, fetch() wiring
  test_dothebay_fetcher.py        parsing: fixture, missing venue/address, malformed URLs, fetch() wiring
  test_db.py                     upsert/dedupe/query/delete/update, combined filters
  test_integration.py            full pipeline, idempotency, CLI end-to-end, per-fetcher failure isolation
  test_web.py                     dashboard API: CRUD, filters, 404s, isolated per-test DB
```

## Setup

```bash
cd sf_event_curator
pip install -r requirements.txt -r requirements-dev.txt
python3 -m pytest -q          # 71 tests, no network required

python3 -m sf_event_curator.cli fetch   # pulls from all configured sources into the store
python3 -m uvicorn sf_event_curator.web:app --reload   # dashboard at http://127.0.0.1:8000
```

Default DB location: `~/.sf_event_curator/events.db` for both the CLI and the
dashboard. Override with `--db path` for the CLI, or the `SF_EVENT_CURATOR_DB`
env var for the dashboard — they point at the same file by default, so
`cli fetch` populates what the dashboard shows.

## Dashboard

Single page at `/`, backed by a REST API:

- `GET /api/events` — filters: `start_after`, `start_before`, `free_only`, `category`
- `POST /api/events` — create a manual event (e.g. an Ocean Beach bonfire you're planning)
- `PATCH /api/events/{id}` — partial update
- `DELETE /api/events/{id}` — remove

Fetched and manually-added events live in the same table (`source` column
distinguishes `funcheap_sf` from `manual`) so they show up together, filter
together, and manual entries can't collide with fetched ones even if IDs
happen to match — the dedup key is `(source, source_id)`.

## Test coverage notes

- All 71 tests run offline — `test_integration.py` and `test_web.py` monkeypatch
  the network/use isolated temp DBs, so `fetch`, `list`, the dashboard API, and
  repeated-run idempotency are all verified without hitting anything live.
  `test_integration.py` also verifies that one fetcher failing (simulated
  outage or a broken parser) never blocks the others, and that a fragile
  source returning 0 events triggers a stderr warning.
- `test_db.py` documents current query semantics that are easy to get wrong later:
  filters are ANDed together, category matching is substring (`LIKE '%x%'`), rows
  with a null `start_ts` sort first in ascending order, and the same `source_id`
  from two different sources is intentionally treated as two distinct events.
- A real gotcha worth knowing if you add a third fetcher: fetchers that each
  `import urllib.request` share the exact same global module object, so
  monkeypatching `urlopen` via two separate `module.urllib.request.urlopen`
  paths in the same test just overwrites one global symbol twice — the
  second patch silently wins for *all* fetchers. `test_integration.py`'s
  `fake_network` fixture uses one URL-dispatching fake instead.

## Why SF Funcheap first

Its RSS feed (`https://sf.funcheap.com/rssfeed2/`) carries a custom
`funCheap:*` namespace with structured start/end times, cost, venue,
address, and categories — far richer than plain RSS, and no API key
needed. It skews free/cheap and citywide, which is a good first source.

## Additional sources evaluated

- **DoTheBay** (`fetchers/dothebay.py`) — added, but genuinely fragile.
  DoTheBay has no public RSS or API — its only `/feed` endpoint is a
  logged-in user's personal activity feed, not an events feed. This
  fetcher scrapes the server-rendered HTML instead, parsing by **anchor
  href URL pattern** (`/events/YYYY/M/D/slug`, `/venues/slug`) rather than
  CSS classes or DOM structure, since routing patterns are far more stable
  than template markup. Known limitations, by design:
  - No time-of-day (DoTheBay doesn't encode it in a parseable way) — every
    event's `start` is midnight on the correct date.
  - No cost or category data — left at defaults (`""`, `[]`).
  - **The test fixture is hand-built, not captured.** This sandbox can't
    reach `dothebay.com` directly (network is allowlisted to dev domains
    only), and the fetch tool available here returns pre-processed content,
    not raw HTML — so unlike Funcheap's fixture, `dothebay_sample.html` is
    a synthetic approximation of the real markup, not a verified real
    response. **Run `cli fetch` on your own machine and check the output
    before trusting this in production** — if DoTheBay's real DOM differs
    from what's modeled here in ways invisible to the visible link
    structure, this will pass tests here but fail silently in practice.
  - `cli fetch` treats DoTheBay as a "fragile source": if it returns 0
    events, a warning prints to stderr rather than failing silently, and a
    DoTheBay failure never blocks Funcheap (or any other fetcher) from
    running — see `FRAGILE_SOURCES` and the try/except in `cli.py`.

- **Eventbrite** — skipped. Their public event-search API was shut down in
  2020; it now only lists events you already own, so it can't discover
  arbitrary public SF events.

- **Secret San Francisco** — skipped. It's an editorial blog ("50 things to
  do this month" articles), not a structured events database. There's no
  per-event feed to parse; extracting real event data would mean scraping
  prose and using an LLM to pull structured facts out of it — a
  fundamentally less reliable pipeline than RSS or even URL-pattern
  scraping, and a bigger lift than seemed worth it alongside DoTheBay.

- **SF.gov / SF Rec & Parks** — no public RSS/iCal feed found; the
  calendar page requires JavaScript rendering, which points toward browser
  automation rather than a simple fetcher. Still a candidate if browser
  automation becomes part of this project.

## Adding a source

Implement `Fetcher` (`name: str`, `fetch() -> list[Event]`) in
`fetchers/`, add an instance to `FETCHERS` in `cli.py`. If it's scraped
rather than RSS/API-based, add its `name` to `FRAGILE_SOURCES` too, so a
silent drop to zero results gets flagged. Candidates for next: SF Rec &
Parks calendar (would need browser automation), NOAA tide/weather data
for outdoor-event suitability (e.g. Ocean Beach bonfire nights).

## Deployment (self-hosted)

These steps assume your usual setup: a self-hosted Ubuntu Linux box. This
runs the dashboard as a persistent systemd service and schedules the weekly
fetch via cron, kept as two separate mechanisms since they have different
jobs - one keeps a process alive, the other runs a one-shot task on a
schedule.

**1. Get the code and dependencies onto the machine**, then create a venv:

```bash
cd /path/to/sf_event_curator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python3 -m pytest -q   # confirm it's healthy on this machine before deploying
deactivate
```

**2. Install the systemd service** so the dashboard survives reboots and
restarts itself if it crashes:

```bash
# edit deploy/sf-event-curator.service first - replace <YOUR_USER>,
# <PROJECT_DIR>, and <VENV_PYTHON> with your actual values (see comments
# in the file for exactly what each one should be)
sudo cp deploy/sf-event-curator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sf-event-curator
sudo systemctl status sf-event-curator   # confirm it's running
```

It binds to `127.0.0.1` only, on purpose - there's no auth on this dashboard
yet (see "Not yet built" below), so it shouldn't be reachable from your LAN
or the internet without something in front of it. The service file's
comments cover how to change that safely if you want LAN access later.

**3. Add the weekly fetch to cron:**

```bash
chmod +x scripts/weekly_fetch.sh   # already executable in this delivery, but harmless to confirm
crontab -e
# paste in the line from deploy/crontab.txt, with <YOUR_USER> replaced
```

Verified in this sandbox: the cron expression (`0 9 * * MON`) does fire
every Monday at 9am - checked programmatically across 5 consecutive weeks,
not just eyeballed. The wrapper script's logging was also verified for
real, including its most important edge case: when I ran it here, both
fetchers genuinely failed (this sandbox can't reach either site), and the
script logged `funcheap_sf: FAILED (...)` and `dothebay: FAILED (...)` with
timestamps and still exited 0 - which is *correct* behavior, but worth
knowing: **cron's own failure-alerting won't fire even if every single
source fails**, since `cli fetch` never propagates that as a non-zero exit
code. If you want an actual alert when fetches are silently failing, change
that exit-code behavior first rather than relying on cron's `MAILTO`.

**What I could not verify from this sandbox:** whether the systemd unit
actually starts a real, reachable dashboard, and whether the DoTheBay
scraper's real network fetch works. This environment's network is
allowlisted to a small set of dev domains - `dothebay.com` isn't in it (a
live fetch attempt from here returns HTTP 403), and neither is a way to
expose a running server back to you. Both need to be checked on your actual
machine after deploying. `systemctl status sf-event-curator` and a
`curl http://127.0.0.1:8000/` right after enabling the service are the
quickest way to confirm.

## Deployment (Oracle Cloud Always Free)

Same project, same systemd service and cron job as the self-hosted section
above - what's different on a cloud instance is networking (two separate
firewalls to open, not one) and the fact that the dashboard is now
internet-facing, which is why nginx + basic auth is not optional here the
way it was optional on a home LAN.

1. **Create the instance.** Compute → Instances → Create Instance in the OCI
   console. `VM.Standard.A1.Flex` (Ampere ARM) is the best value in the
   Always Free tier, but commonly fails with "out of host capacity" in busy
   regions - `VM.Standard.E2.1.Micro` (AMD, 1GB RAM) is a reliable fallback
   and plenty for this app. Use Ubuntu, upload your SSH public key at
   creation time.

2. **Open port 80 (and 443 for HTTPS) in the Security List** -
   Networking → Virtual Cloud Networks → your VCN → Security Lists → add an
   ingress rule, source `0.0.0.0/0`, destination port 80/443. Port 22 (SSH)
   is open by default; this one isn't.

3. **Fix the Ubuntu image's own iptables rules.** This is the single most
   common stumbling block: OCI's stock Ubuntu image blocks everything
   except port 22 at the OS level, *independent* of the Security List you
   just edited - both have to allow the traffic. SSH in and run:
   ```bash
   sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
   sudo apt install iptables-persistent -y
   sudo netfilter-persistent save
   ```
   If the site still doesn't load after nginx is running despite a correct
   Security List, this is almost always why.

4. **Get the project on the instance and set it up** exactly as in the
   self-hosted steps above (venv, install, `pytest -q`), then install the
   systemd service unchanged - it still binds to `127.0.0.1` only, which is
   what makes step 5 sufficient instead of needing auth built into the app.

5. **Install the nginx reverse proxy with basic auth**
   (`deploy/nginx-sf-event-curator.conf`) - full setup instructions are in
   the file's own comments (site config, `htpasswd`, `nginx -t`). This is
   the piece that makes it acceptable to expose this publicly: nobody
   reaches the dashboard without a password first.

6. **Add the weekly cron job** - same `deploy/crontab.txt` entry, just with
   the path updated to wherever the project lives on the instance
   (e.g. `/home/ubuntu/sf_event_curator`).

**Verified in this sandbox:** the nginx config's brace/directive syntax
(checked manually, since this sandbox can't install nginx itself to run
`nginx -t`), and the crontab expression (see the self-hosted section above).
**Not verified, and can't be from here:** whether OCI's actual Security
List UI matches this description exactly (console UIs shift over time),
whether the free-tier capacity issue mentioned in step 1 is currently
affecting your region, and whether the full reverse-proxy chain (browser →
nginx → basic auth → FastAPI) actually works end-to-end on a real instance.
Run `sudo nginx -t` before every reload, and hit the site from a browser
(not `curl` from the instance itself) once fully wired up to confirm.

## Deployment (GitHub Pages, read-only)

The lightest-weight option, with one real trade-off worth understanding
before you pick it: **GitHub Pages only serves static files - it can't run
the FastAPI backend at all.** That means no `/api/events` CRUD, and no
shared, persistent "add your own event" (the Ocean Beach bonfire case from
early on). Adding an event on this version would only be possible client-side
with no server to save it to, so this deployment deliberately ships
**read-only** (`docs/index.html`) rather than a broken-looking add form.

What it's good for: a free, zero-maintenance, always-on calendar view, kept
current by a GitHub Actions workflow instead of your own cron job -
`.github/workflows/weekly_fetch.yml` runs the fetchers every Monday, exports
to `docs/data/events.json` via the new `cli export` command, and commits the
result. GitHub Pages serves the `docs/` folder directly.

One genuine upside this uncovers: **Actions runners have real outbound
internet access**, unlike the sandbox this project was built and tested in.
The first few scheduled runs will be the first time `dothebay.py`'s scraper
actually hits the live site rather than the hand-built fixture - worth
watching the Action's logs for whether it still returns events, given
everything said earlier about that fetcher's fragility.

**Setup:**

1. Push this project to a GitHub repo (if you haven't already).
2. Repo Settings → Pages → Source → "Deploy from a branch" → branch `main`,
   folder `/docs`.
3. Repo Settings → Actions → General → Workflow permissions → "Read and
   write permissions" (needed for the workflow to commit the exported JSON
   back to the repo).
4. Either wait for the first scheduled Monday run, or trigger it immediately:
   Actions tab → "Weekly event fetch" → Run workflow.
5. Your site will be at `https://<username>.github.io/<repo-name>/`.

**What I verified from this sandbox:** the full pipeline end-to-end - ran
both fetchers against their fixtures, exported to JSON via the new `export`
command, served `docs/index.html` over real HTTP (not `file://`, since
`fetch()` requires an actual server) and confirmed it correctly loaded and
would render all 7 events. Also confirmed `git-auto-commit-action@v5`
genuinely exists as a tag (checked via `codeload.github.com`, not assumed) -
`github.com` happens to be one of the few external domains this sandbox can
actually reach. **What I could not verify:** the real GitHub Pages
deployment itself, the Action running on GitHub's actual infrastructure
end-to-end, and - most importantly - whether the DoTheBay scraper's parsing
logic holds up against the real live site now that it'll finally get the
chance to run against it.

## Not yet built

- Semantic search / ChromaDB (worth adding once there's enough data to
  search over — plain filters cover most needs for now)
- Curation/recommendation agent
- Partiful draft generator + Discord/alert delivery
- Auth on the dashboard (fine for local-only use; add before exposing it
  beyond your own machine)
