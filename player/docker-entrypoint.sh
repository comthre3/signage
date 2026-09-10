#!/bin/sh
set -e

sed -i "s/__PLAYER_VERSION__/${PLAYER_VERSION:-dev}/g" /usr/share/nginx/html/sw.js

cat > /usr/share/nginx/html/config.js <<EOF
window.API_BASE_URL = "${API_BASE_URL:-}";
window.APP_URL      = "${APP_URL:-}";
EOF

# Let the dashboard — and only the dashboard — frame the player, for the Screens
# preview panel. Falls back to 'none' when unset, so an unconfigured deployment
# stays un-frameable rather than emitting a malformed policy.
sed -i "s|__APP_URL__|${APP_URL:-\'none\'}|g" /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
