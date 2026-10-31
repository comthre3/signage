# Changelog

## Unreleased

### Added
- Multi-zone layouts with draggable split lines and presets.
- Zone editor with per-zone media and durations.
- Player support for multi-zone rendering and per-zone carousels.
- Drag-and-drop media uploads.
- Website URLs as media entries.
- Retro-futuristic dashboard theme.
- New backend endpoints for zones and layout data.
- Freeform zone drawing and snap-to-grid.
- Zone templates (save/apply).
- Per-zone fade duration control.
- Player offline caching (service worker + layout cache).

### Fixed
- Startup no longer wipes `screens.password_hash` and `users.must_change_password`
  on every container boot. Two `UPDATE` statements dating from the initial commit
  ran inside the FastAPI startup hook and blanked both columns on each restart.
  Neither column is reachable from an API today (no endpoint sets a screen
  password; nothing ever sets `must_change_password` to 1), so nothing was being
  lost in practice — but any future work on screen passwords or forced password
  rotation would have silently lost its state on the next deploy. Covered by
  `backend/tests/test_startup_persistence.py`.

### Changed
- Player transitions: fade-in only, no black flash between items.
- Duration `0` keeps a zone static; `>0` advances.
- Player only refreshes zones when layout changes.

### Notes
- Default admin remains `admin` / `admin123` until changed manually.
