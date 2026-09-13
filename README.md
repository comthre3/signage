# Signage

Self-hosted digital signage system with an admin dashboard, multi-zone player, and Docker Compose deployment.

## Quick start

```bash
./scripts/redeploy.sh
```

This is the supported way to stand the stack up, on this machine or a new one.
It checks every prerequisite, creates and validates `.env` (generating strong
secrets on first run), derives the host-specific settings, rebuilds, waits for
health, and verifies the deployment before reporting success. It is safe to
re-run: `data/` and `uploads/` are never touched.

```bash
./scripts/redeploy.sh --check-only   # validate this host, change nothing
./scripts/redeploy.sh --no-build     # restart without rebuilding images
./scripts/redeploy.sh --yes          # skip the confirmation prompt
```

- Admin dashboard: `http://<host>:3000`
- Player: `http://<host>:3001`
- API: `http://<host>:8000`
- Landing: `http://<host>:3003`

On a fresh install the admin password is generated and printed once by
`redeploy.sh`. It must be at least 12 characters; the backend refuses to seed
the first admin otherwise.

`scripts/install.sh` still works and simply calls `redeploy.sh`.

## Requirements

Docker with the Compose plugin (or `docker-compose`), `git`, and `curl`.
`redeploy.sh --check-only` reports anything missing.

## Backup script

```bash
chmod +x scripts/backup.sh
./scripts/backup.sh
```

## Key features

- Media library with drag-and-drop uploads.
- Playlists and screen assignment.
- Multi-zone layouts with draggable split lines.
- Freeform zones (draw by dragging on the canvas).
- Grid snapping and templates for reusing layouts.
- Per-zone media carousels with independent durations.
- Website URLs as media (rendered in player via iframe).
- Offline-friendly player caching (app shell + uploads).
- Menus content type (menus → categories → items, bilingual EN/AR) rendered to PNG boards via an internal renderer service, then dropped straight into a playlist.

## Zone behavior

- Set **duration = 0** to keep the zone static (no fade, no auto-advance).
- Set **duration > 0** to cycle items at the specified interval with fade-in.
- Videos loop automatically when duration > 0.
- Per-zone fade duration can be adjusted (Fade ms).

## Multi-zone editor

Go to **Screens** → select a screen → click **Zones**:

- Presets: 2 columns, 3 columns, 2 rows, hero + side, single zone.
- Drag zone handles on all sides to resize.
- Drag the zone area to move it.
- Drag on empty canvas to draw a new zone.
- Add media per zone and set durations.
- Click **Save Zones**.
- Save and apply templates across screens.
- Use snap-to-grid for clean alignment.

## Website media

In **Media Library**, use **Add Website**:

- `https://` or `http://` only.
- Website entries render in the player as iframes.

## Menus + renderer service

Menus (menus → categories → items, bilingual EN/AR) are turned into PNG boards by an internal
`renderer` service (headless Chromium), which the backend calls over HTTP. It's built from
`renderer/` in `docker-compose.yml`, runs alongside `backend`, and has no published host port —
only the backend can reach it, on the compose network.

Set these in `.env` before rendering will work:

```
RENDERER_URL=http://renderer:8080
RENDERER_TOKEN=<a-shared-secret>
```

- `RENDERER_URL` — internal URL of the `renderer` service. Leave unset to disable menu rendering
  (`GET /ai/capabilities` reports `"menus": false` and the dashboard hides the Menus section).
  With both variables unset, the Menus section stays hidden and everything else works normally.
- `RENDERER_TOKEN` — shared secret the backend sends to the renderer on every request; must match
  the `RENDERER_TOKEN` the `renderer` service itself is started with. The `renderer` service fails
  closed (rejects every request) if this is unset when it starts.

The backend does not depend on `renderer` at startup — it calls it lazily per request and degrades
gracefully if it's unreachable — so build and start the renderer explicitly, *before* deploying the
backend, whenever you're turning menu rendering on for the first time or updating the renderer image:

```bash
sudo docker-compose build renderer && sudo docker-compose up -d renderer
```

Only then redeploy the backend as usual (`sudo docker-compose up -d --build backend`). This keeps a
routine backend deploy from also having to build the ~2 GB Playwright renderer image.

## Tailscale / remote access

Set these in `.env` if you access via a Tailnet hostname:

```
API_BASE_URL=http://<tailscale-hostname-or-ip>:8000
PLAYER_BASE_URL=http://<tailscale-hostname-or-ip>:3001
```

Then rebuild:

```bash
sudo docker-compose up -d --build
```

## Connection mode toggle

You can switch between local/tailscale and Cloudflare testing without changing `.env`:

- Open **Connection Settings** in the dashboard header.
- Set **Mode** to `Cloudflare`.
- Enter API/Player base URLs.
- Save and refresh.

## Backup

```bash
sudo docker-compose down
tar -czf signage-backup-$(date +%F).tar.gz data/ uploads/ /home/ahmed/signage
sudo docker-compose up -d
```

## Notes

- This repo currently uses SQLite and local volumes.
- For production hardening, see `PROJECT_SCOPE.md`.
