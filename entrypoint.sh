#!/usr/bin/env bash

case "$1" in
  johnny)
    echo -n "Starting johnny.. "
    if [ $FLASK_DEBUG -ne 1 ]; then
      echo "production"
      gunicorn -w $(nproc) -b 0.0.0.0:8000 --preload "johnny:create_app()"
    else
      echo "debug"
      /usr/local/bin/flask --app johnny:create_app run --host 0.0.0.0 --port 8000
    fi
  ;;
  worker)
    echo "WORKER"
    celery -A johnny.tasks:clry worker
  ;;
  scheduler)
    echo "SCHEDULER"
    celery -A johnny.tasks:clry beat -s /tmp/celerybeat-schedule
  ;;
  bash)
    bash
  ;;
  *)
    echo "Unknown command"
  ;;
esac
