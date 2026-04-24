#!/bin/sh
set -e

cat > /usr/share/nginx/html/config.js <<EOF
window.APP_URL = "${APP_URL:-https://app.khanshoof.com}";
EOF

exec nginx -g 'daemon off;'
