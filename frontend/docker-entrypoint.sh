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

# ── Cache-busting ───────────────────────────────────────────────────────────
# index.html and the JS are served no-store, but styles.css and the mascot PNGs
# carry a 7-day max-age. So a deploy pairs brand-new scripts with a stale
# stylesheet, and every returning user sees the new UI with none of its styling
# until their cache expires. That is how the menu preview shipped as a 300x150
# default-sized iframe cropped in the corner of its panel.
# Stamp each with a hash of its own bytes: the URL changes exactly when the
# content does, so a deploy is atomic and an unchanged asset still hits cache.
# Same approach as landing/docker-entrypoint.sh.
ROOT=/usr/share/nginx/html
ASSET_VERSION=$(cat "$ROOT/styles.css" "$ROOT"/assets/faces/*.png 2>/dev/null | md5sum | cut -c1-10)
sed -i \
  -e "s|href=\"styles.css\"|href=\"styles.css?v=${ASSET_VERSION}\"|g" \
  -e "s|\"assets/faces/\([a-z0-9_]*\)\.png\"|\"assets/faces/\1.png?v=${ASSET_VERSION}\"|g" \
  "$ROOT/index.html"
echo "frontend: asset version ${ASSET_VERSION}"

exec nginx -g 'daemon off;'
