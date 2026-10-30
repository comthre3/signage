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

# Point connect-src / img-src / media-src / frame-src at the API origin the
# player calls. Same parsing as frontend/docker-entrypoint.sh: scheme://host[:port]
# only, plain hostname characters only, production API when unset or
# unparseable, and the ws(s) origin derived from it (http -> ws, https -> wss).
API_ORIGIN=$(printf '%s\n' "${API_BASE_URL:-}" | sed -nE 's,^(https?://[A-Za-z0-9.-]+(:[0-9]+)?)([/?#].*)?$,\1,p')
if [ -z "$API_ORIGIN" ]; then
  [ -n "${API_BASE_URL:-}" ] && echo "player: API_BASE_URL '${API_BASE_URL}' is not an http(s) URL; CSP falls back to the production API"
  API_ORIGIN=https://api.khanshoof.com
fi
sed -i \
  -e "s|__API_ORIGIN__|${API_ORIGIN}|g" \
  -e "s|__API_WS_ORIGIN__|ws${API_ORIGIN#http}|g" \
  /etc/nginx/conf.d/default.conf
echo "player: CSP API origin ${API_ORIGIN}"

exec nginx -g 'daemon off;'
