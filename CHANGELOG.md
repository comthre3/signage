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

### Changed
- Player transitions: fade-in only, no black flash between items.
- Duration `0` keeps a zone static; `>0` advances.
- Player only refreshes zones when layout changes.

### Notes
- Default admin remains `admin` / `admin123` until changed manually.
