#!/bin/sh
set -e

cat > /usr/share/nginx/html/config.js <<EOF
window.API_BASE_URL = "${API_BASE_URL:-}";
window.PLAYER_BASE_URL = "${PLAYER_BASE_URL:-}";
window.WALLS_PHASE2_ENABLED = ${WALLS_PHASE2_ENABLED:-false};
EOF

# Point the CSP's frame-src at the same player origin the app frames for the
# Screens preview. Falls back to 'none' when unset, so an unconfigured
# deployment gets a valid restrictive policy rather than a malformed one.
sed -i "s|__PLAYER_BASE_URL__|${PLAYER_BASE_URL:-\'none\'}|g" /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
