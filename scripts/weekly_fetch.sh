#!/usr/bin/env bash
# Weekly fetch job for sf_event_curator. Intended to run from cron.
#
# What it does: activates the project's venv (if present), runs `cli fetch`
# against the DB the dashboard reads from, and appends a timestamped record
# of the run - including stderr, so per-fetcher failures and the
# zero-results warning for fragile sources (see cli.py FRAGILE_SOURCES) show
# up in the log rather than vanishing into cron's default /dev/null.
#
# Exit code mirrors the fetch command's, so cron's own failure-mail
# mechanism (MAILTO=...) fires only if `cli fetch` itself exits non-zero -
# which currently it never does, even on total network failure, since every
# fetcher's exceptions are caught. If you want cron to actually alert you on
# repeated failures, that's the thing to change first (e.g. exit non-zero
# when zero sources succeeded), not this script.

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${HOME}/.sf_event_curator"
LOG_FILE="${LOG_DIR}/fetch.log"

mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR" || { echo "$(date -Iseconds) FATAL: could not cd to $PROJECT_DIR" >> "$LOG_FILE"; exit 1; }

if [ -f "${PROJECT_DIR}/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "${PROJECT_DIR}/.venv/bin/activate"
fi

{
  echo "=== $(date -Iseconds) starting weekly fetch ==="
  python3 -m sf_event_curator.cli fetch
  status=$?
  echo "=== $(date -Iseconds) finished, exit code $status ==="
  echo ""
} >> "$LOG_FILE" 2>&1

exit "$status"
