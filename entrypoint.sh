#!/usr/bin/env bash
# entrypoint.sh — dispatch on argv[0] for the three-container layout.
# See compose.yaml: johnny-web, johnny-api, johnny-tasks all use this image
# with a different `command:`.

set -euo pipefail

WEB_PORT="${WEB_PORT:-8000}"
API_PORT="${API_PORT:-8001}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
PRUNE_INTERVAL_SECONDS="${PRUNE_INTERVAL_SECONDS:-86400}"
SENTINEL="${MIGRATION_SENTINEL:-/data/.migrated}"

case "${1:-}" in
  web)
    exec gunicorn -w "$(nproc)" -b "0.0.0.0:${WEB_PORT}" 'johnny.web:create_app()'
    ;;

  api)
    exec uvicorn 'johnny.api:create_app' --factory \
        --host 0.0.0.0 --port "${API_PORT}"
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
