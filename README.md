# Signage

Self-hosted digital signage system with an admin dashboard, multi-zone player, and Docker Compose deployment.

## Quick start

```bash
sudo docker-compose up -d --build
```

- Admin dashboard: `http://<host>:3000`
- Player: `http://<host>:3001`
- API: `http://<host>:8000`

Default login: `admin` / `admin123`

## Installation script

```bash
chmod +x scripts/install.sh
./scripts/install.sh
```

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
