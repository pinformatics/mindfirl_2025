#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Load environment variables when running locally or from simple process managers.
if [[ -f ".env" ]]; then
  set -a
  source .env
  set +a
fi

: "${PORT:=8000}"
: "${WEB_CONCURRENCY:=2}"
: "${GUNICORN_TIMEOUT:=120}"

exec gunicorn \
  --bind "0.0.0.0:${PORT}" \
  --workers "${WEB_CONCURRENCY}" \
  --timeout "${GUNICORN_TIMEOUT}" \
  --access-logfile - \
  --error-logfile - \
  app:app
