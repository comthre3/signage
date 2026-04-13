#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker first."
  exit 1
fi

if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  COMPOSE="docker compose"
fi

SUDO=""
if ! $COMPOSE ps >/dev/null 2>&1; then
  SUDO="sudo"
fi

if [ ! -f "$ROOT_DIR/.env" ]; then
  cat > "$ROOT_DIR/.env" <<'EOF'
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123
DATABASE_URL=sqlite:///./data/signage.db
UPLOAD_DIR=/app/uploads
ALLOWED_ORIGINS=*
SESSION_TTL_SECONDS=86400
PREVIEW_TTL_SECONDS=300
MAX_UPLOAD_MB=50
API_BASE_URL=
PLAYER_BASE_URL=
EOF
  echo "Created .env with defaults."
fi

mkdir -p "$ROOT_DIR/data" "$ROOT_DIR/uploads"

echo "Building and starting stack..."
(cd "$ROOT_DIR" && $SUDO $COMPOSE up -d --build)

echo "Done."
echo "Dashboard: http://<host>:3000"
echo "Player:    http://<host>:3001"
echo "API:       http://<host>:8000"
