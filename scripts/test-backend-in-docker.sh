#!/usr/bin/env bash
# Run the backend test suite inside the built backend image, against the
# sawwii_test database on the compose network. Works from any checkout or
# worktree (mounts this checkout's backend/ over /app), needs no host Python.
#
#   bash scripts/test-backend-in-docker.sh              # full suite
#   bash scripts/test-backend-in-docker.sh tests/test_menus.py -v
#   KHAN_TEST_DB=sawwii_test_walls KHAN_TEST_REDIS_DB=3 bash scripts/test-backend-in-docker.sh
#
# Runs sharing a database corrupt each other: conftest truncates every table at
# session start. Concurrent runs (other worktrees, other sessions) therefore
# each need their own KHAN_TEST_DB (created on first use; the name must contain
# "test") and their own KHAN_TEST_REDIS_DB.
#
# The DSN is pinned to sawwii_test here AND re-pinned by tests/conftest.py, so
# a run can never touch the production database. REDIS_URL is pinned to logical
# db 1 for the same reason: rate-limit counters are keyed by api_key id, and a
# test minting an id that collides with a live key would otherwise spend that
# customer's real quota.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
env_file="${KHAN_ENV_FILE:-/home/ahmed/signage/.env}"
pw="$(grep '^POSTGRES_PASSWORD=' "$env_file" | cut -d= -f2- | tr -d '"')"
[ -n "$pw" ] || { echo "POSTGRES_PASSWORD not found in $env_file" >&2; exit 2; }
image="${KHAN_BACKEND_IMAGE:-signage_backend:latest}"
network="${KHAN_NETWORK:-signage_default}"
db="${KHAN_TEST_DB:-sawwii_test}"
case "$db" in *test*) ;; *) echo "KHAN_TEST_DB must contain 'test': $db" >&2; exit 2 ;; esac
redis_db="${KHAN_TEST_REDIS_DB:-1}"
[ "$redis_db" != 0 ] || { echo "KHAN_TEST_REDIS_DB 0 is production's" >&2; exit 2; }
postgres="${KHAN_POSTGRES_CONTAINER:-signage_postgres_1}"
docker exec "$postgres" psql -U sawwii -d sawwii -Atc \
  "SELECT 1 FROM pg_database WHERE datname = '$db'" | grep -q 1 \
  || docker exec "$postgres" createdb -U sawwii "$db"
exec docker run --rm --network "$network" \
  -v "$here/backend:/app" -w /app \
  -e DATABASE_URL="postgresql://sawwii:${pw}@postgres:5432/${db}" \
  -e TEST_DATABASE_URL="postgresql://sawwii:${pw}@postgres:5432/${db}" \
  -e REDIS_URL="redis://redis:6379/${redis_db}" \
  -e RATE_LIMITS_ENABLED=0 -e UPLOAD_DIR=/tmp/uploads -e DEV_MODE=1 \
  "$image" python -m pytest -q -p no:cacheprovider "$@"
