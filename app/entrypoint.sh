#!/bin/sh
set -eu

if [ "${SERVICE_MODE:-api}" = "worker" ]; then
  exec python -m app.worker
fi

exec gunicorn app.main:app \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --worker-class uvicorn.workers.UvicornWorker \
  --timeout "${GUNICORN_TIMEOUT:-180}" \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile -
