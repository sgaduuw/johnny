# johnny

[![ci](https://github.com/sgaduuw/johnny/actions/workflows/ci.yml/badge.svg)](https://github.com/sgaduuw/johnny/actions/workflows/ci.yml)
[![ghcr](https://ghcr-badge.egpl.dev/sgaduuw/johnny/latest_tag?ignore=sha-*&label=ghcr&color=blue)](https://github.com/sgaduuw/johnny/pkgs/container/johnny)

A web app for browsing the **state of an Ansible-managed fleet**.
Hosts push their facts and play results to johnny via a callback
plugin; johnny stores them and renders dashboards.

Pairs with **[sgaduuw/johnny-callback][cb]**, the Ansible collection
that produces the wire payloads. Together: johnny is the receiver
and viewer; johnny-callback is the sender wired into your Ansible
controller.

[cb]: https://github.com/sgaduuw/johnny-callback

## What it shows you

- **Groups**: the front page is a card grid of every Ansible group
  observed across your fleet, each card showing the group's name,
  current member count, and an optional operator-set description.
  `all` is pinned first; the rest follow alphabetically. Click a
  card to drill into the group's hosts at `/g/<group>/`. When the
  callback plugin reports inventory topology (callback >= 0.3.0
  against server >= 0.5.0), the detail page also shows the group's
  ancestry chain (e.g. `webservers ⊂ linux ⊂ all`) and its direct
  child groups.
- **Hosts**: every host that's ever been touched by a play, with
  IPv4, OS, kernel, virt role/type, memory, vCPUs, uptime
  (snapshot), and how long ago you last saw it. Click through to
  `/h/<fqdn>/` for the full `ansible_facts` dump and the history of
  fact snapshots.
- **Playbooks**: every play johnny has received, with status
  (running/finished/failed/abandoned), duration, user, inventory, and
  tags. ABANDONED is johnny's verdict for plays whose controller went
  silent for longer than `JOHNNY_PLAYBOOK_STALE_AFTER_SECONDS`; any
  subsequent ingest revives the row. Click through for the per-host
  roster and the per-task event timeline.

Read-only UI. All writes happen via the callback plugin POSTing to
the api tier.

### Search and sort

Every list page (groups index, hosts in a group, playbooks) carries
a search box and sortable column headers. Updates swap in place via
HTMX; the URL pushes on every change so deep links and the back
button preserve state.

The search box accepts a small `key:value` grammar:

```
nginx                          bare term, OR'd across the
                               default columns for that list
user:eelco                     scoped match
status:failed,unreachable      comma-OR within a scope
name:"deploy nginx"            quote whitespace
ip:[fe80::1]                   brackets for `:`-bearing values
                               (IPv6 literals)
user:eelco status:failed nginx all of the above, AND'd together
```

Hover the input for the per-list scope vocabulary. Scopes per list:

| Page             | Scopes                                      |
|------------------|---------------------------------------------|
| `/`              | `name`, `description`                       |
| `/playbooks`     | `name`, `user`, `status`, `inventory`       |
| `/g/<group>/`    | `fqdn`, `os`, `kernel`, `virt`, `ip`        |

Unknown scopes degrade to bare terms — `weh:foo` matches as the
literal string rather than silently disappearing.

## Status

v0.4.3 (2026-06-11). Pairs with
[johnny-callback v0.2.1][cb-rel].

[cb-rel]: https://galaxy.ansible.com/ui/repo/published/sgaduuw/johnny/

Suitable for personal homelab use today. Designed to scale to the
~10k-host work-fleet case via a Postgres swap (same code, change
`DATABASE_URL`); not yet deployed at that scale.

## Quickstart with Docker Compose

Set a bearer token (must match the one in your Ansible controller's
`JOHNNY_API_TOKEN`) and start the stack:

```sh
JOHNNY_API_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(32))') \
    docker compose up -d
```

Three containers come up:

- `johnny-web` (port 8000): read UI
- `johnny-api` (port 8001): callback ingest endpoint
- `johnny-tasks`: alembic migrate + retention prune sidecar

Then on your Ansible controller, install the callback plugin:

```sh
ansible-galaxy collection install sgaduuw.johnny:0.2.1
```

…and configure it with the same token (`JOHNNY_API_TOKEN`) and
johnny-api's URL (`JOHNNY_API_URL`). See the
[johnny-callback README][cb] for the full `ansible.cfg` snippet.

Run any playbook. The callback plugin POSTs facts/events/stats at
`v2_playbook_on_stats`; johnny-web shows them at
`http://localhost:8000`.

## Architecture

```
ansible-playbook (controller)
        |
        | sgaduuw.johnny.callback collection
        | buffers per-play, flushes on v2_playbook_on_stats
        v
  POST  /api/v1/playbooks                    start
  POST  /api/v1/playbooks/{id}/facts         per-host fact snapshots
  POST  /api/v1/playbooks/{id}/events        per-task per-host results
  POST  /api/v1/playbooks/{id}/finish        stats summary
        |
        v
  johnny-api  (FastAPI)
        |
        v
   SQLite (dev/homelab) or Postgres (10k+ hosts)
        ^                       ^
        |                       |
  johnny-web                  johnny-tasks
  (Flask + Pico)              (alembic + prune sidecar)
```

- **One image, three commands.** `ghcr.io/sgaduuw/johnny:VERSION`
  dispatched via `entrypoint.sh` argv into `web` / `api` / `tasks`.
- **`johnny-tasks` owns migrations.** It runs `alembic upgrade head`
  once per data volume, gated by a sentinel file at
  `/data/.migrated`. `johnny-web` and `johnny-api` `depends_on:
  service_healthy` against that sentinel, so they only start once
  the schema is current.
- **Bearer token only on `johnny-api`.** The web tier is read-only
  and either runs behind a reverse proxy that handles user auth or
  is exposed only on the tailnet. johnny doesn't try to be its own
  identity provider.
- **SQLite by default, Postgres-ready.** Generated columns use a
  dialect-portable `json_path()` helper; `DateTime` columns use a
  `UtcDateTime` TypeDecorator. The same model definitions emit
  correct DDL for both dialects.

## Configuration

Loaded from `.env` (gitignored) or shell environment.

| Var                     | Where                 | Default                       |
|-------------------------|-----------------------|-------------------------------|
| `DATABASE_URL`          | all three containers  | `sqlite:////data/johnny.db`   |
| `JOHNNY_API_TOKEN`      | `johnny-api` only     | required (no default)         |
| `JOHNNY_VERSION`        | host (compose lookup) | `0.1.0`                       |
| `WEB_WORKERS`           | `johnny-web`          | `$(nproc)` inside container   |
| `API_WORKERS`           | `johnny-api`          | `1` (SQLite-safe; bump on PG) |
| `RETENTION_DAYS`        | `johnny-tasks`        | `30`                          |
| `PRUNE_INTERVAL_SECONDS`| `johnny-tasks`        | `86400` (24 h)                |
| `WEB_PORT`              | host (compose port)   | `8000`                        |
| `API_PORT`              | host (compose port)   | `8001`                        |
| `JOHNNY_TRUNCATE_CHARS` | `johnny-web`          | `15` (cell ellipsis budget)   |
| `JOHNNY_PLAYBOOK_STALE_AFTER_SECONDS` | `johnny-tasks` | `3600` (sec until RUNNING → ABANDONED) |
| `MARK_ABANDONED_INTERVAL_SECONDS` | `johnny-tasks` | `900` (sweeper cadence; not in `.env`) |
| `ABANDONED_PRUNE_DAYS` | `johnny-tasks` | `90` (delete ABANDONED rows older than this; not in `.env`) |

`johnny-api` returns 503 on every request if `JOHNNY_API_TOKEN` is
unset — intentional fail-loud, never silently accepts unauthenticated
ingest.

## Scope and non-goals

- **One-shot POSTs at end of play.** Live-tailing a running play is
  out of v1 scope. The plugin batches and flushes once at
  `v2_playbook_on_stats`.
- **No write surfaces in the UI.** johnny-web is strictly read-only.
  All state mutations happen via the api tier (callback ingest) or
  the tasks sidecar (retention pruning).
- **No identity provider.** Authenticate the web tier behind your
  existing reverse proxy / Tailscale / SSO setup.
- **stdout per task is capped at 4 KB, diff at 16 KB.** Full
  capture is opt-in roadmap, not v1.

## Development

```sh
uv sync
uv run pytest                                       # 294 tests, ~1s
uv run ruff check johnny/ tests/
uv run alembic upgrade head                         # apply schema
uv run uvicorn 'johnny.api:create_app' --factory \
    --host 0.0.0.0 --port 8001                      # api tier
uv run gunicorn -w 2 -b 0.0.0.0:8000 \
    'johnny.web:create_app()'                       # web tier
uv run johnny prune --older-than-days 30 \
    --abandoned-older-than-days 90              # CLI sweep
uv run johnny mark-abandoned                        # mark stale RUNNING as ABANDONED
uv run johnny groups-rebuild                        # backfill groups
uv run johnny group describe webservers "..."       # set description
uv run python scripts/seed_mock.py                  # demo fleet data
```

The Dockerfile has separate `test` and `prod` stages; CI builds
both and runs `pytest --cov-fail-under=85` inside the test image
on every push, then publishes the prod image to
`ghcr.io/sgaduuw/johnny` on non-PR runs.

## License

MIT — see [LICENSE](LICENSE).
