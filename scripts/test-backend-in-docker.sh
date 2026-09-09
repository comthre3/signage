#!/usr/bin/env bash
# Run the backend test suite inside the built backend image, against the
# sawwii_test database on the compose network. Works from any checkout or
# worktree (mounts this checkout's backend/ over /app), needs no host Python.
#
#   bash scripts/test-backend-in-docker.sh              # full suite
#   bash scripts/test-backend-in-docker.sh tests/test_menus.py -v
#
# The DSN is pinned to sawwii_test here AND re-pinned by tests/conftest.py, so
# a run can never touch the production database.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
env_file="${KHAN_ENV_FILE:-/home/ahmed/signage/.env}"
pw="$(grep '^POSTGRES_PASSWORD=' "$env_file" | cut -d= -f2- | tr -d '"')"
[ -n "$pw" ] || { echo "POSTGRES_PASSWORD not found in $env_file" >&2; exit 2; }
image="${KHAN_BACKEND_IMAGE:-signage_backend:latest}"
network="${KHAN_NETWORK:-signage_default}"
exec docker run --rm --network "$network" \
  -v "$here/backend:/app" -w /app \
  -e DATABASE_URL="postgresql://sawwii:${pw}@postgres:5432/sawwii_test" \
  -e RATE_LIMITS_ENABLED=0 -e UPLOAD_DIR=/tmp/uploads -e DEV_MODE=1 \
  "$image" python -m pytest -q -p no:cacheprovider "$@"
