#!/usr/bin/env bash
# End-to-end scan-to-pair test: a TV (the player) and a phone (the dashboard)
# pair against a throwaway backend with its own Postgres and Redis. The player
# and dashboard are built from this working tree; the backend is the current
# image (BACKEND_IMAGE to override). Nothing touches production: the browsers'
# khanshoof.com requests are answered by these containers.
#
#   scripts/e2e-scan-to-pair/run.sh            screenshots in scripts/e2e-scan-to-pair/shots/
#   scripts/e2e-scan-to-pair/run.sh --wall     three TVs on a video wall (e2e_wall.py; see run_wall)
#
# Needs Docker and the signage_renderer image (Playwright). Containers are named
# e2e-pair-* (e2e-wall-* with --wall) and removed by name when the script ends.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

# ---- --wall ------------------------------------------------------------------
# Three TVs on one video wall, set up through the API (e2e_wall.py). Loopback
# mode: the TVs load the player from http://127.0.0.1:13022, a player container
# whose API_BASE_URL is the local API (127.0.0.1:18020). Playwright's routing
# never sees WebSockets, so a TV loaded as https://play.khanshoof.com would open
# its wall socket to PRODUCTION.
#
# The backend is always built from this tree as signage_backend:e2e;
# BACKEND_IMAGE is ignored here (the run tests this branch's walls, and
# :latest is production's tag, never retagged). WEB_CONCURRENCY=4, as in
# production, so a placement PUT and a TV's socket usually land on different
# workers and the move travels through Redis. Names, network and ports differ
# from the pair run's, so the two can run side by side.
#
#   PLAYER_DIR         player to build (default: this tree's player/)
#   E2E_WALL_REPLACE=1 remove leftover e2e-wall-* containers (only your own killed run's)
wall_containers=(e2e-wall-api e2e-wall-db e2e-wall-redis e2e-wall-player e2e-wall-ffmpeg)

cleanup_wall() {
  docker rm -f "${wall_containers[@]}" > /dev/null 2>&1 || true
  docker network rm e2e-wall-net > /dev/null 2>&1 || true
}

run_wall() {
  local player_dir="${PLAYER_DIR:-$repo/player}"
  # Another session's wall run uses these names and ports, and its containers
  # come up over minutes: db and redis first, the API only after the image
  # builds and the test video. Removing any of them would break that run
  # halfway, so every e2e-wall-* name of the stack (not only e2e-wall-api) is
  # checked before the trap is set.
  local busy
  busy="$(docker ps -a --format '{{.Names}}' \
    | grep -E '^e2e-wall-(api|db|redis|player|app|ffmpeg)$' | tr '\n' ' ' || true)"
  if [[ -n "$busy" && "${E2E_WALL_REPLACE:-0}" != 1 ]]; then
    echo "e2e-wall containers exist: another wall run is in progress, or a killed one left them:" >&2
    # shellcheck disable=SC2086  # $busy is a list of container names
    docker inspect -f '  {{.Name}}  created {{.Created}}  {{.State.Status}}' $busy >&2 || true
    echo "Wait for it. E2E_WALL_REPLACE=1 removes them: use it only for your own killed run's" >&2
    echo "leftovers (no container runs e2e_wall.py, and they were created when that run started)." >&2
    exit 1
  fi
  trap cleanup_wall EXIT
  cleanup_wall

  echo "Starting a throwaway wall stack…"
  docker network create e2e-wall-net > /dev/null
  # 4 workers x (THREADPOOL_SIZE 40 + 1) = 164 connections, over Postgres's
  # default of 100 (budget in backend/db.py; docker-compose.yml uses 200 too).
  docker run -d --name e2e-wall-db --network e2e-wall-net --tmpfs /var/lib/postgresql/data \
    -e POSTGRES_USER=e2e -e POSTGRES_PASSWORD=e2e -e POSTGRES_DB=e2e postgres:16-alpine \
    -c max_connections=200 > /dev/null
  docker run -d --name e2e-wall-redis --network e2e-wall-net redis:7-alpine > /dev/null

  echo "Building signage_backend:e2e and signage_player:e2e-wall…"
  docker build -q -t signage_backend:e2e "$repo/backend" > /dev/null
  docker build -q -t signage_player:e2e-wall "$player_dir" > /dev/null

  # The 20 s 5760x1080 test video: red, green and blue thirds with digits
  # 1, 2, 3 and a white bar sweeping across. Made once (shots/ is gitignored),
  # with the ffmpeg in the backend image; drawbox has no time variable, so the
  # bar is an overlay. Written under a temporary name, so an interrupted run
  # never leaves a truncated video behind.
  mkdir -p "$here/shots"
  if [[ ! -s "$here/shots/wall-test.mp4" ]]; then
    echo "Making the test video…"
    local digit="drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:fontsize=600:fontcolor=black:x=(w-tw)/2:y=(h-th)/2:text="
    docker run --rm --name e2e-wall-ffmpeg --network none --user "$(id -u):$(id -g)" \
      -v "$here/shots":/out --entrypoint ffmpeg signage_backend:e2e -hide_banner -loglevel error -y \
      -f lavfi -i "color=c=0xFF0000:s=1920x1080:r=30:d=20" \
      -f lavfi -i "color=c=0x00FF00:s=1920x1080:r=30:d=20" \
      -f lavfi -i "color=c=0x0000FF:s=1920x1080:r=30:d=20" \
      -f lavfi -i "color=c=0xFFFFFF:s=96x1080:r=30:d=20" \
      -filter_complex "[0]${digit}1[a];[1]${digit}2[b];[2]${digit}3[c];[a][b][c]hstack=inputs=3[bg];[bg][3]overlay=x='mod(t*288\,5760)':y=0:shortest=1,format=yuv420p[v]" \
      -map "[v]" -t 20 -c:v libx264 -preset veryfast -crf 23 -profile:v high -movflags +faststart \
      /out/wall-test.tmp.mp4
    mv "$here/shots/wall-test.tmp.mp4" "$here/shots/wall-test.mp4"
  fi

  for _ in $(seq 1 30); do docker exec e2e-wall-db pg_isready -U e2e -d e2e > /dev/null 2>&1 && break; sleep 1; done
  docker run -d --name e2e-wall-api --network e2e-wall-net -p 127.0.0.1:18020:8000 --tmpfs /app/uploads --tmpfs /app/data \
    -e DATABASE_URL=postgresql://e2e:e2e@e2e-wall-db:5432/e2e -e REDIS_URL=redis://e2e-wall-redis:6379/0 \
    -e ADMIN_USERNAME=demo -e ADMIN_PASSWORD=Khanshoof-Demo-2026 -e SECRET_KEY="e2e-$RANDOM-$RANDOM" \
    -e ALLOWED_ORIGINS=https://app.khanshoof.com,https://play.khanshoof.com,http://127.0.0.1:13022,http://127.0.0.1:13023 \
    -e APP_URL=https://app.khanshoof.com -e API_BASE_URL=http://127.0.0.1:18020 \
    -e PLAYER_BASE_URL=http://127.0.0.1:13022 -e UPLOAD_DIR=/app/uploads -e DATA_DIR=/app/data \
    -e RATE_LIMITS_ENABLED=false -e WEB_CONCURRENCY=4 signage_backend:e2e > /dev/null
  # APP_URL stays production's dashboard: the TV's QR still reads
  # https://app.khanshoof.com/pair?code=..., which the test only decodes.
  docker run -d --name e2e-wall-player -p 127.0.0.1:13022:80 -e PLAYER_VERSION=e2e-wall \
    -e API_BASE_URL=http://127.0.0.1:18020 -e APP_URL=https://app.khanshoof.com \
    signage_player:e2e-wall > /dev/null

  local up=0
  for _ in $(seq 1 90); do
    if curl -fsS http://127.0.0.1:18020/health > /dev/null 2>&1 \
        && curl -fsS -o /dev/null http://127.0.0.1:13022/ > /dev/null 2>&1; then up=1; break; fi
    sleep 1
  done
  if [[ "$up" != 1 ]]; then
    echo "The wall stack never came up. e2e-wall-api, last 80 log lines:" >&2
    docker logs --tail 80 e2e-wall-api >&2 || true
    exit 1
  fi

  # QR decoder and PIL for the test, installed once into .pylib (gitignored).
  if [[ ! -d "$here/.pylib/zxingcpp.libs" && ! -f "$here/.pylib/zxingcpp.cpython-312-x86_64-linux-gnu.so" ]]; then
    docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$here":/w --entrypoint pip signage_renderer:latest \
      install -q --target /w/.pylib zxing-cpp==2.3.0 pillow==11.3.0
  fi

  echo "Three TVs on a video wall…"
  if ! docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp --network host -v "$here":/w -w /w \
      -e E2E_API=http://127.0.0.1:18020 -e E2E_PLAY_LAN=http://127.0.0.1:13022 \
      --entrypoint python signage_renderer:latest -I e2e_wall.py; then
    echo "e2e_wall.py failed. e2e-wall-api, last 80 log lines:" >&2
    docker logs --tail 80 e2e-wall-api >&2 || true
    exit 1
  fi
}

if [[ "${1:-}" == "--wall" ]]; then
  run_wall
  exit 0
fi

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
