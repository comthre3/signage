#!/bin/sh
set -e

ROOT=/usr/share/nginx/html

cat > "$ROOT/config.js" <<EOF
window.APP_URL = "${APP_URL:-https://app.khanshoof.com}";
EOF

# ── Cache-busting ───────────────────────────────────────────────────────────
# index.html is served no-store, but styles.css and the mascot PNGs are cached
# (nginx sets a max-age, and Cloudflare caches them at the edge). Without a
# version stamp a deploy pairs brand-new markup with a stale stylesheet, which
# renders a visibly broken page for returning visitors until the cache expires.
# Stamp both with a hash of their own bytes: the URL changes exactly when the
# content does, so a deploy is atomic and an unchanged asset still hits cache.
ASSET_VERSION=$(cat "$ROOT/styles.css" "$ROOT"/assets/faces/*.png 2>/dev/null | md5sum | cut -c1-10)

sed -i \
  -e "s|href=\"styles.css\"|href=\"styles.css?v=${ASSET_VERSION}\"|g" \
  -e "s|\"assets/faces/\([a-z0-9_]*\)\.png\"|\"assets/faces/\1.png?v=${ASSET_VERSION}\"|g" \
  "$ROOT/index.html"

echo "landing: asset version ${ASSET_VERSION}"

exec nginx -g 'daemon off;'
