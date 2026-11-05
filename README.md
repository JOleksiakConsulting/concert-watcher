# Concert Watcher

[![CI](https://github.com/JOleksiakConsulting/concert-watcher/actions/workflows/ci.yml/badge.svg)](https://github.com/JOleksiakConsulting/concert-watcher/actions/workflows/ci.yml)

A local concert watcher for Windows and macOS: Python, SQLite, an HTTP API and
an e-mail digest (which will be sent in Polish). Docker is optional.

## Implementation status

- Ticketmaster Discovery Feed adapter
- Artist matching (exact + fuzzy)
- Canonical events + cross-source deduplication
- Notification outbox + `/api/v1/notifications/*`
- Local SMTP runner with a preview mode; notifications are acknowledged only after delivery
- Knockout (pagination, support acts, event details) and Fource (listing, support acts, statuses) adapters

Ticketmaster, Knockout and Fource are enabled by default; other adapters are
disabled until implemented. See [Limitations](#limitations) for the current scope.

## Quick start (local)

Uses [uv](https://docs.astral.sh/uv/) (`brew install uv` on macOS) and Python 3.12+.
Dependencies are resolved with `exclude-newer = "2 weeks"` (see `[tool.uv]` in `pyproject.toml`).

```bash
cp .env.example .env
# In .env set APP_API_KEY and TICKETMASTER_API_KEY (if using Ticketmaster).
uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8182
```

On Windows, run `Copy-Item .env.example .env` in PowerShell, then the same `uv`
commands. Migrations create the `data` directory automatically. Run all
commands from the project directory. The API runs as a single process; keep
its terminal open while the digest runner is working.

You don't need a Ticketmaster key if you set `DIGEST_SOURCES=knockout,fource`.
Add artists at `http://127.0.0.1:8182/docs` (fill in the `X-API-Key` field of
the chosen operation with the value of `APP_API_KEY`).

Settings are read at startup, so restart the API (or the container) after
editing `.env`. Never commit `.env`.

## Quick start (Docker)

```bash
cp .env.example .env
mkdir -p data
docker compose up -d --build
curl http://localhost:8182/health
docker compose logs --tail=100 concert-watcher
```

Compose sets `DATABASE_URL` to `/data` inside the container and publishes the
API on `127.0.0.1:8182` only. The container runs as a non-root user with
UID 1000. On Linux, make sure `./data` is writable by that UID.

## Ticketmaster API key

Only needed for the Ticketmaster source.

1. Sign in at [developer.ticketmaster.com](https://developer.ticketmaster.com/).
2. Go to **My Apps → Create New App**.
3. Copy the **Consumer Key** into `TICKETMASTER_API_KEY` in `.env`.

To check the key without leaving it in your shell history:

```bash
read -r -s -p "Ticketmaster Consumer Key: " TM_API_KEY; echo
curl --fail --silent --show-error -G \
  "https://app.ticketmaster.com/discovery-feed/v2/events.json" \
  --data-urlencode "apikey=${TM_API_KEY}" \
  --data-urlencode "countryCode=PL" | head -c 500; echo
unset TM_API_KEY
```

A JSON body with `"events": [...]` means the key works. HTTP `401` points to a
wrong key or an inactive app, `429` to rate limiting. An empty `events` list is
a valid response and does not indicate a key problem.

## API

All endpoints except `/health` require the `X-API-Key` header.

```bash
export API_KEY='value of APP_API_KEY from .env'

# Add artist
curl -X POST http://localhost:8182/api/v1/artists \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $API_KEY" \
  -d '{"canonical_name": "Chelsea Wolfe", "countries": ["PL"], "aliases": []}'

# Trigger scan
curl -X POST http://localhost:8182/api/v1/scan \
  -H "X-API-Key: $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"country_codes": ["PL"], "sources": ["ticketmaster"]}'

unset API_KEY
```

### Watched artists

The list is stored in the application database. Swagger UI at
`http://127.0.0.1:8182/docs` supports the same operations as the API:

```text
GET    /api/v1/artists
POST   /api/v1/artists
GET    /api/v1/artists/{id}
PATCH  /api/v1/artists/{id}
DELETE /api/v1/artists/{id}
```

Editing an artist:

```bash
curl -X PATCH http://127.0.0.1:8182/api/v1/artists/1 \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $API_KEY" \
  -d '{"aliases": ["CHELSEA WOLFE"], "countries": ["PL"]}'
```

Use `aliases` when a source spells the artist differently. `DELETE` is a soft
delete: it disables the artist (`enabled=false`) but keeps the history of
events and notifications.

### Scan results

`POST /api/v1/scan` returns a summary per source:

```json
{
  "sources": {
    "ticketmaster": {"status": "ok", "events_found": 1234}
  },
  "matched_events": 1,
  "new_events": 1,
  "notifications_created": 1
}
```

| Field | Meaning |
| --- | --- |
| `events_found` | Events returned by the source |
| `matched_events` | Events matching your watched artists |
| `new_events` | New canonical (deduplicated) events |
| `notifications_created` | Alerts added to the notification outbox |

A source that fails does not abort the scan; its status explains why:

| Status | Meaning |
| --- | --- |
| `ok` | Source scanned successfully |
| `skipped` | Source disabled with `SOURCE_<NAME>_ENABLED=false` |
| `degraded` | Source not configured (e.g. missing `TICKETMASTER_API_KEY`) or not implemented yet |
| `error` | Timeout, HTTP `401/403/429/5xx`, malformed JSON or an unexpected page structure |

Running the same scan again should report `new_events: 0` and
`notifications_created: 0`. If a known event changes its date, venue or
status, a separate change notification is created instead. A second scan
started while one is still running gets HTTP `409`.

### Notifications

```text
GET  /api/v1/notifications/pending
POST /api/v1/notifications/{id}/ack
POST /api/v1/notifications/{id}/fail
```

The digest runner uses these endpoints; you only need them for manual testing.

## Local daily digest

The runner works on your machine: it calls the local API, builds a single
digest and sends it over SMTP. In a second terminal, from the project
directory, run (the same command works in Windows PowerShell and the macOS
terminal):

```bash
# first, check the result without sending e-mail and without ACK/FAIL
uv run python -X utf8 -m app.cli.daily_digest --dry-run

# normal run: scan → pending → e-mail → ACK
uv run python -X utf8 -m app.cli.daily_digest
```

The legacy `bash scripts/daily-digest.sh` still works.

Configure in `.env`:

```env
DIGEST_API_URL=http://127.0.0.1:8182
DIGEST_COUNTRY_CODES=PL
DIGEST_SOURCES=ticketmaster,knockout,fource
DIGEST_HTTP_TIMEOUT_SECONDS=600

SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=...
SMTP_PASSWORD=...
SMTP_FROM=concert-watcher@example.com
SMTP_TO=you@example.com
SMTP_USE_TLS=true
SMTP_USE_SSL=false
TICKETMASTER_HTTP_TIMEOUT_SECONDS=120
```

`--dry-run` does not need SMTP settings. It runs the scan and fetches pending
notifications, but does not send e-mail and does not ACK/FAIL anything.
Because the scan stores new events, it can create a `NEW_EVENT` notification,
so it is not fully read-only.

If delivery fails, the runner calls `/fail`, so alerts stay pending. ACK happens
only after the e-mail is sent successfully. If a batch of ACKs is interrupted
midway, the acknowledged and still-pending IDs are written to the log, and the
pending ones may be sent again on the next run (at-least-once delivery).
`SMTP_PASSWORD` stays in your local `.env` only. Setting both
`SMTP_USE_TLS=true` and `SMTP_USE_SSL=true` is detected before the scan and
the runner exits with code 2.

When sources report problems and the queue is empty, the runner prints
diagnostics and exits with code 1. This does not confirm that there are no new
concerts. If there are pending notifications, the digest is still sent and
includes the source problems.

### Scheduling

The runner can be scheduled with Windows Task Scheduler, cron or launchd. The
API must be running and the computer must be on.

- **Windows Task Scheduler:** set the program to the full path of `uv.exe`, the
  arguments to `run python -X utf8 -m app.cli.daily_digest` and *Start in* to
  the project directory. Disallow parallel runs of the task.
- **macOS / Linux:** run the same command with the project directory as the
  working directory.

## Promoter sites

Knockout and Fource are read from their public listing pages. The shared
crawler checks `robots.txt` before the listing, keeps a delay between requests
(`CRAWLER_MIN_DELAY_SECONDS`), retries a bounded number of times honouring
`Retry-After`, refuses redirects to other domains and redirect loops, and reports a changed page
structure as an error instead of silently returning no events.

## Limitations

- Local, single-user setup: SQLite and a single API process on `127.0.0.1`.
- Scheduling is left to the operating system; there is no built-in scheduler.
- Do not run two digest runners at the same time. The scan lock does not cover
  SMTP delivery, and notifications are delivered at least once.
- Knockout and Fource cover Poland only. Events in unrecognised cities are not
  assigned a country.
- eBilet, Eventim, Going and Live Nation adapters are not implemented yet and
  are disabled by default.
- No monitoring and no conditional HTTP requests (ETag / `If-Modified-Since`) yet.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ticketmaster: degraded` | Empty `TICKETMASTER_API_KEY` | Set it in `.env` and restart the API |
| `<source>: skipped` | Source disabled in `.env` | Enable it or change the requested `sources` |
| `<source>: error` | Timeout, HTTP error or changed page | Check the logs and the key test above; don't retry `401/403` without fixing the configuration |
| `events_found: 0` | Empty feed, rate limit or wrong country | Run the key test above; an empty feed can be valid |
| `events_found > 0`, `matched_events: 0` | Artist not listed by the source under that name | Add `aliases` matching the source's spelling |
| HTTP `409` on `/scan` | Previous scan still running | Wait a few seconds |
| Database error locally | Wrong `DATABASE_URL` | Use `sqlite+aiosqlite:///./data/concert-watcher.db` |
| Database error in Docker | Missing or unwritable `./data` mount | `mkdir -p data` before `docker compose up`; check `docker compose logs` |

## Tests

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Regular tests use recorded fixtures and HTTP mocks. Separate live tests read
public websites, without touching the user database and without sending
e-mail:

```bash
# macOS
RUN_LIVE_TESTS=1 uv run pytest tests/test_promoter_adapters.py -m live -q
```

```powershell
# Windows PowerShell
$env:RUN_LIVE_TESTS = '1'
uv run pytest tests/test_promoter_adapters.py -m live -q
Remove-Item Env:RUN_LIVE_TESTS
```

## License

[MIT](LICENSE)
