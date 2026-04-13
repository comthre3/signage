# Project Scope

## Purpose
Deliver a self-hosted digital signage system that lets an admin manage screens, playlists, and media, and lets players display assigned content reliably.

## In Scope
- Backend API for auth, screens, playlists, media, and preview.
- Admin dashboard for managing content and screens.
- Player app for pairing and playback.
- Docker Compose deployment for a single VM.
- Environment-driven configuration (API base URL, player base URL, session TTL).
- Operational basics (health checks, backups).
- Multi-zone layouts with adjustable split lines.
- Website URLs as media entries.
- Templates and grid snapping for zones.
- Offline-friendly player caching.
- Local + remote access support (e.g., Tailscale) via configurable base URLs.

## Out of Scope
- Multi-tenant SaaS features.
- Advanced scheduling, multi-zone layouts, or template editors.
- OAuth / SSO integrations.
- Device management beyond pairing and playback.

## Guardrails
- Keep UI simple and focused on core workflows.
- Avoid introducing new infrastructure dependencies without approval.
- Prefer configuration via `.env` or container env vars.
- Prioritize reliability and clear error feedback.
