#!/bin/sh
set -e

cat > /usr/share/nginx/html/config.js <<EOF
window.APP_URL = "${APP_URL:-http://192.168.18.192:3000}";
EOF

exec nginx -g 'daemon off;'
