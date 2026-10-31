#!/usr/bin/env bash
# End-to-end scan-to-pair test: a TV (the player) and a phone (the dashboard)
# pair against a throwaway backend with its own Postgres and Redis. The player
# and dashboard are built from this working tree; the backend is the current
# image (BACKEND_IMAGE to override). Nothing touches production: the browsers'
# khanshoof.com requests are answered by these containers.
#
#   scripts/e2e-scan-to-pair/run.sh            screenshots in scripts/e2e-scan-to-pair/shots/
#
# Needs Docker and the signage_renderer image (Playwright). Containers are named
# e2e-pair-* and removed by name when the script ends.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
backend_image="${BACKEND_IMAGE:-signage_backend:latest}"
containers=(e2e-pair-api e2e-pair-db e2e-pair-redis e2e-pair-player e2e-pair-app)

cleanup() {
  docker rm -f "${containers[@]}" > /dev/null 2>&1 || true
  docker network rm e2e-pair-net > /dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

echo "Starting a throwaway stack…"
docker network create e2e-pair-net > /dev/null
docker run -d --name e2e-pair-db --network e2e-pair-net --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=e2e -e POSTGRES_PASSWORD=e2e -e POSTGRES_DB=e2e postgres:16-alpine > /dev/null
docker run -d --name e2e-pair-redis --network e2e-pair-net redis:7-alpine > /dev/null
for _ in $(seq 1 30); do docker exec e2e-pair-db pg_isready -U e2e -d e2e > /dev/null 2>&1 && break; sleep 1; done

docker run -d --name e2e-pair-api --network e2e-pair-net -p 127.0.0.1:18000:8000 --tmpfs /app/uploads --tmpfs /app/data \
  -e DATABASE_URL=postgresql://e2e:e2e@e2e-pair-db:5432/e2e -e REDIS_URL=redis://e2e-pair-redis:6379/0 \
  -e ADMIN_USERNAME=demo -e ADMIN_PASSWORD=Khanshoof-Demo-2026 -e SECRET_KEY="e2e-$RANDOM-$RANDOM" \
  -e ALLOWED_ORIGINS=https://app.khanshoof.com,https://play.khanshoof.com \
  -e APP_URL=https://app.khanshoof.com -e API_BASE_URL=https://api.khanshoof.com \
  -e PLAYER_BASE_URL=https://play.khanshoof.com -e UPLOAD_DIR=/app/uploads -e DATA_DIR=/app/data \
  -e RATE_LIMITS_ENABLED=false -e WEB_CONCURRENCY=1 "$backend_image" > /dev/null

docker build -q -t signage_player:e2e "$repo/player" > /dev/null
docker build -q -t signage_frontend:e2e "$repo/frontend" > /dev/null
docker run -d --name e2e-pair-player -p 127.0.0.1:13001:80 -e PLAYER_VERSION=e2e \
  -e API_BASE_URL=https://api.khanshoof.com -e APP_URL=https://app.khanshoof.com signage_player:e2e > /dev/null
docker run -d --name e2e-pair-app -p 127.0.0.1:13000:80 -e API_BASE_URL=https://api.khanshoof.com \
  -e PLAYER_BASE_URL=https://play.khanshoof.com -e WALLS_PHASE2_ENABLED=true signage_frontend:e2e > /dev/null

for _ in $(seq 1 60); do curl -fsS http://127.0.0.1:18000/health > /dev/null 2>&1 && break; sleep 1; done

# QR decoder for the test, installed once into .pylib (gitignored).
if [[ ! -d "$here/.pylib/zxingcpp.libs" && ! -f "$here/.pylib/zxingcpp.cpython-312-x86_64-linux-gnu.so" ]]; then
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$here":/w --entrypoint pip signage_renderer:latest \
    install -q --target /w/.pylib zxing-cpp==2.3.0 pillow==11.3.0
fi

mkdir -p "$here/shots"
echo "Pairing a TV with a phone…"
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp --network host -v "$here":/w -w /w \
  --entrypoint python signage_renderer:latest -I e2e_pair.py
