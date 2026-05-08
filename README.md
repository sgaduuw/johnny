# johnny

[![ci](https://github.com/sgaduuw/johnny/actions/workflows/ci.yml/badge.svg)](https://github.com/sgaduuw/johnny/actions/workflows/ci.yml)
[![ghcr](https://ghcr-badge.egpl.dev/sgaduuw/johnny/latest_tag?label=ghcr&color=blue)](https://github.com/sgaduuw/johnny/pkgs/container/johnny)

A web app for browsing the **state of an Ansible-managed fleet**.
Hosts push their facts and play results to johnny via a callback
plugin; johnny stores them and renders dashboards.

Pairs with **[sgaduuw/johnny-callback][cb]**, the Ansible collection
that produces the wire payloads. Together: johnny is the receiver
and viewer; johnny-callback is the sender wired into your Ansible
controller.

[cb]: https://github.com/sgaduuw/johnny-callback

## What it shows you

- **Hosts**: every host that's ever been touched by a play, with
  IPv4, virt role/type, memory, vCPUs, uptime (snapshot), and how
  long ago you last saw it. Click through for the full
  `ansible_facts` dump and the history of fact snapshots.
- **Playbooks**: every play johnny has received, with status
  (running/finished/failed), duration, user, inventory, and tags.
  Click through for the per-host roster and the per-task event
  timeline.

Read-only UI. All writes happen via the callback plugin POSTing to
the api tier.

## Status

v0.1.0 (2026-05-08). First tagged release; pairs with
[johnny-callback v0.1.0][cb-rel].

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
ansible-galaxy collection install sgaduuw.johnny:0.1.0
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
| `RETENTION_DAYS`        | `johnny-tasks`        | `30`                          |
| `PRUNE_INTERVAL_SECONDS`| `johnny-tasks`        | `86400` (24 h)                |
| `WEB_PORT`              | host (compose port)   | `8000`                        |
| `API_PORT`              | host (compose port)   | `8001`                        |

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
poetry install
poetry run pytest                                       # 71 tests, ~1s
poetry run ruff check johnny/ tests/
poetry run alembic upgrade head                         # apply schema
poetry run uvicorn 'johnny.api:create_app' --factory \
    --host 0.0.0.0 --port 8001                          # api tier
poetry run gunicorn -w 2 -b 0.0.0.0:8000 \
    'johnny.web:create_app()'                           # web tier
poetry run johnny prune --older-than-days 30            # CLI sweep
```

The Dockerfile has separate `test` and `prod` stages; CI builds
both and runs `pytest --cov-fail-under=85` inside the test image
on every push, then publishes the prod image to
`ghcr.io/sgaduuw/johnny` on non-PR runs.

## License

MIT — see [LICENSE](LICENSE).
