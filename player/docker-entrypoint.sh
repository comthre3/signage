#!/bin/sh
set -e

html=/usr/share/nginx/html

cat > "$html/config.js" <<EOF
window.API_BASE_URL = "${API_BASE_URL:-}";
window.APP_URL      = "${APP_URL:-}";
EOF

# The service-worker version names the cache the player shell is served from,
# so it must change whenever any shell file does -- config.js, just written,
# included -- and not only when PLAYER_VERSION was bumped, or TVs keep serving
# the old shell for ever. The nginx config is included too: the cached
# index.html carries its Content-Security-Policy, so a CSP-only change would
# otherwise never reach a TV. Hashed before the placeholders below are filled
# in, so a container restart (which re-runs this script) leaves it unchanged.
shell_hash=$(cat "$html/index.html" "$html/player.js" "$html/styles.css" \
  "$html/config.js" "$html/i18n.js" "$html"/i18n/*.json "$html/vendor/qrcode.js" \
  "$html/sw.js" /etc/nginx/conf.d/default.conf | md5sum | cut -c1-8)
sed -i "s/__PLAYER_VERSION__/${PLAYER_VERSION:-dev}-${shell_hash}/g" "$html/sw.js"
sed -i "s|__API_BASE_URL__|${API_BASE_URL:-}|g" "$html/sw.js"

# Let the dashboard — and only the dashboard — frame the player, for the Screens
# preview panel. Falls back to 'none' when unset, so an unconfigured deployment
# stays un-frameable rather than emitting a malformed policy.
sed -i "s|__APP_URL__|${APP_URL:-\'none\'}|g" /etc/nginx/conf.d/default.conf

# Allow the configured API origin (and its websocket twin) in the CSP.
api_origin=$(printf '%s' "${API_BASE_URL:-}" | sed -nE 's#^(https?://[^/?]+).*#\1#p')
api_ws_origin=$(printf '%s' "$api_origin" | sed 's#^http#ws#')
sed -i "s|__API_ORIGIN__|${api_origin}|g; s|__API_WS_ORIGIN__|${api_ws_origin}|g" \
  /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
