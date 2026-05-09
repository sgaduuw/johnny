#!/usr/bin/env bash
# entrypoint.sh — dispatch on argv[0] for the three-container layout.
# See compose.yaml: johnny-web, johnny-api, johnny-tasks all use this image
# with a different `command:`.

set -euo pipefail

WEB_PORT="${WEB_PORT:-8000}"
API_PORT="${API_PORT:-8001}"
# Worker counts. Web defaults to one per CPU since it's read-only and
# WAL handles concurrent readers. Api defaults to 1 because SQLite is
# a single-writer engine and concurrent gunicorn/uvicorn workers will
# contend on the WAL lock; bump only when the DB is Postgres or when
# you have measured contention. See CONTEXT.md "Container split".
WEB_WORKERS="${WEB_WORKERS:-$(nproc)}"
API_WORKERS="${API_WORKERS:-1}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
PRUNE_INTERVAL_SECONDS="${PRUNE_INTERVAL_SECONDS:-86400}"
SENTINEL="${MIGRATION_SENTINEL:-/data/.migrated}"

case "${1:-}" in
  web)
    # gunicorn defaults to no access log; force both streams to stdout/
    # stderr so `docker compose logs johnny-web` shows per-request lines.
    exec gunicorn -w "${WEB_WORKERS}" -b "0.0.0.0:${WEB_PORT}" \
        --access-logfile - --error-logfile - \
        'johnny.web:create_app()'
    ;;

  api)
    exec uvicorn 'johnny.api:create_app' --factory \
        --host 0.0.0.0 --port "${API_PORT}" --workers "${API_WORKERS}" \
        --log-level info --access-log
    ;;

  tasks)
    # Always run alembic upgrade head: it's a no-op when the schema
    # is already at head, and it's the only way new migrations get
    # applied across a container restart on an existing data volume.
    # The earlier sentinel-gates-the-upgrade design left every
    # migration after the first one un-applied (johnny issue with
    # v0.2.2's projection-column migration sitting unapplied was the
    # surfacing case).
    echo "tasks: running alembic upgrade head"
    alembic upgrade head
    # Sentinel still serves the web/api startup gate (their
    # depend_on healthcheck waits for `test -f $SENTINEL`). Touch
    # it on first start; subsequent starts leave it in place.
    if [ ! -f "${SENTINEL}" ]; then
      touch "${SENTINEL}"
      echo "tasks: first-start sentinel written to ${SENTINEL}"
    fi

    # Periodic retention sweep. Plain loop is intentional — we explicitly
    # rejected celery/APScheduler in v2 (see CONTEXT.md "Why no Celery").
    echo "tasks: prune loop, every ${PRUNE_INTERVAL_SECONDS}s, retention ${RETENTION_DAYS}d"
    while true; do
      johnny prune --older-than-days "${RETENTION_DAYS}" || \
        echo "tasks: prune failed (continuing)"
      sleep "${PRUNE_INTERVAL_SECONDS}"
    done
    ;;

  bash|sh)
    exec bash
    ;;

  *)
    echo "usage: $0 {web|api|tasks|bash}" >&2
    exit 64
    ;;
esac
