#!/bin/sh
set -e

sed -i "s/__PLAYER_VERSION__/${PLAYER_VERSION:-dev}/g" /usr/share/nginx/html/sw.js

cat > /usr/share/nginx/html/config.js <<EOF
window.API_BASE_URL = "${API_BASE_URL:-}";
window.APP_URL      = "${APP_URL:-}";
EOF

exec nginx -g 'daemon off;'
