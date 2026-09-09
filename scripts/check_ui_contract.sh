#!/usr/bin/env bash
# Guardrail for the UI overhaul: app.js's DOM contract must survive markup changes.
set -u
cd "$(dirname "$0")/.."
fail=0

# ── 1. Every id app.js looks up must exist in index.html ──
# (allowlist: nodes app.js creates dynamically at runtime)
DYNAMIC_IDS="canvas-item-detail canvas-items-list canvas-preview-media complete-signup-form complete-signup-overlay"
for id in $(grep -oE 'getElementById\("[a-zA-Z0-9_-]+"\)' frontend/app.js | sed 's/getElementById("//;s/")//' | sort -u); do
  case " $DYNAMIC_IDS " in *" $id "*) continue;; esac
  grep -q "id=\"$id\"" frontend/index.html || { echo "MISSING id in index.html: $id"; fail=1; }
done

# ── 2. Every #id used via querySelector must exist too ──
for id in $(grep -oE 'querySelector(All)?\("#[a-zA-Z0-9_-]+"' frontend/app.js | grep -oE '#[a-zA-Z0-9_-]+' | tr -d '#' | sort -u); do
  case " $DYNAMIC_IDS " in *" $id "*) continue;; esac
  grep -q "id=\"$id\"" frontend/index.html || { echo "MISSING querySelector id in index.html: $id"; fail=1; }
done

# ── 3. Every data-i18n key in index.html must exist in BOTH locale files ──
for key in $(grep -oE 'data-i18n(-placeholder|-aria-label)?="[^"]+"' frontend/index.html | sed 's/.*="//;s/"//' | sort -u); do
  grep -q "\"$key\"" frontend/i18n/en.json || { echo "MISSING en.json key: $key"; fail=1; }
  grep -q "\"$key\"" frontend/i18n/ar.json || { echo "MISSING ar.json key: $key"; fail=1; }
done

# ── 4. nav buttons still carry data-section inside a <nav> ──
for section in sites screens media playlists schedules users walls audit-log api-keys billing; do
  grep -q "data-section=\"$section\"" frontend/index.html || { echo "MISSING nav button: data-section=\"$section\""; fail=1; }
done

[ $fail -eq 0 ] && echo "UI contract OK"
exit $fail
