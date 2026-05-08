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
    # Migration sentinel: alembic upgrade head once per data volume.
    # johnny-web and johnny-api depend_on this container's healthcheck
    # (test -f $SENTINEL) so they only start after the schema is current.
    if [ ! -f "${SENTINEL}" ]; then
      echo "tasks: applying alembic migrations"
      alembic upgrade head
      touch "${SENTINEL}"
      echo "tasks: migrations applied, sentinel written to ${SENTINEL}"
    else
      echo "tasks: sentinel ${SENTINEL} present, skipping alembic"
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
