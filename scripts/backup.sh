#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
TIMESTAMP="$(date +%F_%H-%M-%S)"

mkdir -p "$BACKUP_DIR"

if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  COMPOSE="docker compose"
fi

SUDO=""
if ! $COMPOSE ps >/dev/null 2>&1; then
  SUDO="sudo"
fi

echo "Stopping stack..."
(cd "$ROOT_DIR" && $SUDO $COMPOSE down)

ARCHIVE="$BACKUP_DIR/signage-backup-${TIMESTAMP}.tar.gz"
echo "Creating backup at $ARCHIVE"
tar -czf "$ARCHIVE" "$ROOT_DIR/data" "$ROOT_DIR/uploads" "$ROOT_DIR"

echo "Starting stack..."
(cd "$ROOT_DIR" && $SUDO $COMPOSE up -d)

echo "Backup complete."
