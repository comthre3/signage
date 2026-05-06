#!/bin/sh
set -e

cat > /usr/share/nginx/html/config.js <<EOF
window.API_BASE_URL = "${API_BASE_URL:-}";
window.PLAYER_BASE_URL = "${PLAYER_BASE_URL:-}";
window.WALLS_PHASE2_ENABLED = ${WALLS_PHASE2_ENABLED:-false};
EOF

exec nginx -g 'daemon off;'
