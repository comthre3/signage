#!/usr/bin/env bash
# Guardrail for the UI overhaul: app.js's DOM contract must survive markup changes.
set -u
cd "$(dirname "$0")/.."
fail=0

JS_FILES="frontend/app.js frontend/menus.js frontend/i18n.js"

# ── 1. Every id app.js looks up must exist in index.html ──
# (allowlist: nodes app.js creates dynamically at runtime)
DYNAMIC_IDS="canvas-item-detail canvas-items-list canvas-preview-media complete-signup-form complete-signup-overlay canvas-add-item canvas-item-delete canvas-item-duration canvas-item-save mode-change-cancel mode-change-switch mode-change-typed walls-wizard walls-wizard-cancel menu-back menu-logo-pick menu-logo-clear menu-logo-preview menu-add-cat menu-save menu-render menu-renders-back menu-render-go menu-renders-status menu-renders-grid menu-renders-stale menu-playlist-btn"
for id in $(grep -h -oE 'getElementById\("[a-zA-Z0-9_-]+"\)' $JS_FILES | sed 's/getElementById("//;s/")//' | sort -u); do
  case " $DYNAMIC_IDS " in *" $id "*) continue;; esac
  grep -q "id=\"$id\"" frontend/index.html || { echo "MISSING id in index.html: $id"; fail=1; }
done

# ── 2. Every #id used via querySelector must exist too ──
for id in $(grep -h -oE 'querySelector(All)?\("#[a-zA-Z0-9_-]+"' $JS_FILES | grep -oE '#[a-zA-Z0-9_-]+' | tr -d '#' | sort -u); do
  case " $DYNAMIC_IDS " in *" $id "*) continue;; esac
  grep -q "id=\"$id\"" frontend/index.html || { echo "MISSING querySelector id in index.html: $id"; fail=1; }
done

# ── 3. Every data-i18n key in index.html must exist in BOTH locale files ──
for key in $(grep -oE 'data-i18n(-placeholder|-aria-label)?="[^"]+"' frontend/index.html | sed 's/.*="//;s/"//' | sort -u); do
  grep -q "\"$key\"" frontend/i18n/en.json || { echo "MISSING en.json key: $key"; fail=1; }
  grep -q "\"$key\"" frontend/i18n/ar.json || { echo "MISSING ar.json key: $key"; fail=1; }
done

# ── 4. nav buttons still carry data-section inside a <nav> ──
for section in sites screens media playlists schedules users walls audit-log api-keys billing menus; do
  grep -q "data-section=\"$section\"" frontend/index.html || { echo "MISSING nav button: data-section=\"$section\""; fail=1; }
done

[ $fail -eq 0 ] && echo "UI contract OK"
exit $fail
