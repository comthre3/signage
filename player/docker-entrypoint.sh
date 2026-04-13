#!/bin/sh
set -e

cat > /usr/share/nginx/html/config.js <<EOF
window.API_BASE_URL = "${API_BASE_URL:-}";
EOF

exec nginx -g 'daemon off;'
