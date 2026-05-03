# Multi-Screen Walls Phase 1 — Finishing Plan (Tasks 7–15)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish Phase 1 of the multi-screen walls feature by completing the frontend (admin + player), nginx WebSocket upgrade, and end-to-end smoke + regression.

**Architecture:** Backend is done and green (Tasks 0–6 committed; 123/123 tests passing). Remaining work is purely client-side wiring against the already-shipped REST + WebSocket API, plus one nginx config change and a smoke pass.

**Tech Stack:** Vanilla JS (no framework) admin + player apps; CSS variables already established; i18n via `i18n/{en,ar}.json` files; nginx reverse proxy in front of uvicorn (single worker).

**Source-of-truth plan:** All step-by-step code blocks for Tasks 7–15 already live in `docs/superpowers/plans/2026-05-01-multi-screen-walls-phase1.md`. This finishing plan stitches them into an execution sequence and adds the closing branch-finishing step. **Do not re-derive code — open the original plan at the line ranges below and follow exactly.**

---

## Current state (verified 2026-05-02)

- Branch: `feature/multi-screen-walls-phase1` @ `10a5aea`, pushed to origin.
- Backend tests: **123 passing** (97 baseline + 26 new). Verified via `docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest`.
- Done commits on this branch:
  - `69f1c8e` Task 1 — schema (walls, wall_cells, wall_pairing_codes)
  - `015c157` Task 2 — Wall CRUD endpoints
  - `def7c70` Task 3 — pair-into-cell + redeem + unpair
  - `72d2992` Task 4 — WebSocket endpoint + registry
  - `1557ee8` Task 5 — asyncio tick loop (mirrored same_playlist + synced_rotation)
  - `10a5aea` Task 6 — `/content` exposes `wall_id` + `wall_cell`
- Untracked: `khanshoof_assets/` — leave alone, unrelated to this branch.

---

## Conventions for the remaining tasks

- **Frontend deploys via container rebuild**: `docker-compose build frontend && docker-compose up -d frontend` (admin) or `docker-compose build player && docker-compose up -d player` (player). No source bind-mount, just like backend.
- **Visual smoke after each frontend task**: open `https://app.khanshoof.com` (admin) and `https://play.khanshoof.com` (player) in a browser, watch DevTools console for errors. Type-checking and pytest do not catch UI bugs — looking at it does.
- **Commit at the end of each task** with the exact message in the source-of-truth plan. One task = one commit.
- **Don't run `git add -A`** — stage only files the task touched.
- **Single uvicorn worker** assumption holds (in-memory connection registry). Don't add a second worker without Redis pub/sub.

---

## Task A (= original Task 7): Admin frontend — Walls tab + section markup

**Source plan:** lines **1638–1782** of `docs/superpowers/plans/2026-05-01-multi-screen-walls-phase1.md`.

**Files:**
- Modify: `frontend/index.html` (nav button after `<button data-section="users">` at line 29; Walls section after `#users`)
- Modify: `frontend/styles.css` (append wall grid + editor + pair-code styles)

- [ ] **Step 1:** Add nav button (plan Step 1).
- [ ] **Step 2:** Add `#walls` section markup with editor + pair-modal (plan Step 2).
- [ ] **Step 3:** Append wall-grid + editor + pair-code CSS (plan Step 3).
- [ ] **Step 4:** `docker-compose build frontend && docker-compose up -d frontend`. Open admin in browser, click **Walls**, confirm empty section renders with no console errors (plan Step 4).
- [ ] **Step 5:** Commit `frontend/index.html` + `frontend/styles.css` with the message in plan Step 5.

**Verify location is still accurate:** plan references `<button data-section="users">` at line 29 — confirmed accurate as of 2026-05-02.

---

## Task B (= original Task 8): Admin frontend — list rendering + create-wall wizard

**Source plan:** lines **1784–2017** of the source-of-truth plan.

**Files:**
- Modify: `frontend/app.js` (append Walls module)

- [ ] **Step 1:** Locate existing `data-section` click handler in `frontend/app.js` (plan Step 1).
- [ ] **Step 2:** Append the Walls module (state, fetchers, list renderer, create-wizard) per plan Step 2.
- [ ] **Step 3:** Wire `walls-create-btn` click → wizard; wire wizard submit → `POST /walls` (plan Step 3).
- [ ] **Step 4:** Hook the section show: when `#walls` becomes visible, call `walls.refresh()` (plan Step 4).
- [ ] **Step 5:** Rebuild frontend, log in, create a 1×2 mirrored wall in the UI, confirm it appears in the list (plan Step 5).
- [ ] **Step 6:** Commit with the message in plan Step 6.

---

## Task C (= original Task 9): Admin frontend — wall editor (cells, pair modal, live mosaic)

**Source plan:** lines **2019–2209** of the source-of-truth plan.

**Files:**
- Modify: `frontend/app.js` (extend Walls module with editor + pair flow + live status polling)

- [ ] **Step 1:** Render editor grid from `wall.rows × wall.cols` (plan Step 1).
- [ ] **Step 2:** Click empty cell → `POST /walls/{id}/cells/{r}/{c}/pair-code` → show pair modal with code + countdown (plan Step 2).
- [ ] **Step 3:** Modal "New code" regenerates; "Done" closes (plan Step 3).
- [ ] **Step 4:** Live mosaic on the wall card — poll `/walls/{id}` every 5s, color cells by online/offline (plan Step 4).
- [ ] **Step 5:** "Unpair" button per cell → `DELETE /walls/{id}/cells/{r}/{c}` (plan Step 5).
- [ ] **Step 6:** Rebuild, manually pair a real (or test-token) screen into a cell, confirm mosaic flips green (plan Step 6).
- [ ] **Step 7:** Commit per plan Step 7.

---

## Task D (= original Task 10): Admin i18n keys (EN + AR)

**Source plan:** lines **2211–2337** of the source-of-truth plan.

**Files:**
- Modify: `frontend/i18n/en.json`
- Modify: `frontend/i18n/ar.json`

- [ ] **Step 1:** Add `nav.walls`, `walls.*` key block to `en.json` (plan has the full block).
- [ ] **Step 2:** Add the matching MSA Arabic translations to `ar.json` (plan has the full block — MSA per the bilingual policy).
- [ ] **Step 3:** Rebuild frontend, switch language toggle to AR, confirm RTL layout + Arabic text on the Walls tab + pair modal.
- [ ] **Step 4:** Commit i18n files per plan's commit message.

---

## Task E (= original Task 11): Player — "Have a code from admin?" affordance

**Source plan:** lines **2339–2459** of the source-of-truth plan.

**Files:**
- Modify: `player/index.html` (button + code-entry overlay markup)
- Modify: `player/styles.css` (overlay styles)
- Modify: `player/player.js` (overlay open/close + redeem call)

- [ ] **Step 1:** Add the "Have a code from admin?" button to the player's idle/landing screen (plan Step 1).
- [ ] **Step 2:** Add the code-entry overlay markup + styles (plan Steps 2–3).
- [ ] **Step 3:** Wire submit → `POST /walls/redeem` with the typed code; on success, store `screen_token` in localStorage and call `enterWallMode()` (stub) (plan Step 4).
- [ ] **Step 4:** On error, show the i18n'd error message in the overlay (plan Step 5).
- [ ] **Step 5:** Rebuild player container, open in browser, manually redeem a code generated from admin, confirm overlay flow + token persists (plan Step 6).
- [ ] **Step 6:** Commit per plan's commit message.

---

## Task F (= original Task 12): Player — `enterWallMode` (WebSocket + time-anchor seek + HTTP fallback)

**Source plan:** lines **2461–2667** of the source-of-truth plan.

**Files:**
- Modify: `player/player.js` (new `enterWallMode()` function + WS handlers + drift correction)

- [ ] **Step 1:** Implement `enterWallMode(token)` — connect to `wss://api.khanshoof.com/ws/walls/{token}`, handle `tick` messages with `playlist_index` + `anchor_ts` (plan Step 1).
- [ ] **Step 2:** On every tick, compute `expected_position = (now_ms - anchor_ts) % item_duration`, seek the `<video>` if drift > 250 ms (plan Step 2).
- [ ] **Step 3:** On WS disconnect, exponential backoff + reconnect; if 3 attempts fail, fall back to polling `GET /screens/{token}/content` every 5 s and just play whatever is there (plan Step 3).
- [ ] **Step 4:** On boot, if localStorage has a wall-mode token, auto-`enterWallMode()` instead of the normal pairing screen (plan Step 4).
- [ ] **Step 5:** Rebuild player. Open two browser tabs both wall-mode'd into different cells of the same mirrored wall — they should play the same item, in sync (eyeball ±250 ms) (plan Step 5).
- [ ] **Step 6:** Commit per plan's commit message.

---

## Task G (= original Task 13): Player i18n keys (EN + AR)

**Source plan:** lines **2669–2717** of the source-of-truth plan.

**Files:**
- Modify: `player/i18n/en.json`
- Modify: `player/i18n/ar.json`

- [ ] **Step 1:** Add `wall.*` keys (button label, overlay title, instructions, error messages) to `en.json` per plan.
- [ ] **Step 2:** Add MSA Arabic counterparts to `ar.json` per plan.
- [ ] **Step 3:** Rebuild player, switch to AR, confirm overlay reads RTL + Arabic.
- [ ] **Step 4:** Commit per plan's commit message.

---

## Task H (= original Task 14): Nginx WebSocket upgrade config

**Source plan:** lines **2719–2770** of the source-of-truth plan.

**Files:**
- Modify: nginx config for `api.khanshoof.com` (path TBD — check `nginx/` or `infrastructure/` or wherever the api.khanshoof.com server block lives) **OR** doc-only if Cloudflare Tunnel terminates and upgrades natively.

- [ ] **Step 1:** Determine whether traffic to `api.khanshoof.com` flows through an nginx layer or only Cloudflare Tunnel → uvicorn (plan Step 1).
- [ ] **Step 2:** If nginx is in the path: add the `Upgrade` / `Connection: upgrade` headers to the relevant `location` block per plan Step 2.
- [ ] **Step 3:** If Cloudflare Tunnel only: add a doc comment in `docs/` noting WS works via tunnel without config change (plan Step 3 alt).
- [ ] **Step 4:** Test by opening a wall-mode player and confirming the WS handshake succeeds in DevTools Network tab (status 101 Switching Protocols).
- [ ] **Step 5:** Commit (or skip if doc-only and no file changed; record the finding in the next task's smoke notes).

---

## Task I (= original Task 15): End-to-end smoke + final regression sweep

**Source plan:** lines **2772–2818** of the source-of-truth plan.

**No files modified — verification only.**

- [ ] **Step 1:** Run full backend regression: `docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest 2>&1 | tail -10`. Expected: **123 passed** (no new tests in Tasks 7–15; if a number changed, investigate before continuing).
- [ ] **Step 2:** Manual E2E smoke (plan Step 2):
  - In admin: create a 1×2 wall in mirrored / same_playlist mode with a known playlist.
  - Generate pair codes for both cells.
  - On two browser tabs (or two real TVs), redeem the codes via the player overlay.
  - Confirm both tabs/TVs play the **same media item at the same time** (mirrored sync). Eyeball drift — should be sub-second.
  - In admin mosaic, both cells should show **online** (green).
- [ ] **Step 3:** Switch the wall's mode in admin to `synced_rotation` (if exposed) — confirm both cells advance together.
- [ ] **Step 4:** Kill one player tab — admin mosaic flips that cell to **offline** within ≤5 s (poll interval).
- [ ] **Step 5:** Reload the killed tab — it auto-rejoins (token in localStorage), mosaic flips back to **online**.
- [ ] **Step 6:** Switch admin to AR, repeat one create-wall + pair flow to confirm no Arabic-side regression.
- [ ] **Step 7:** No commit (verification only). Record results in commit message of next step (the finishing step) or in the PR description.

---

## Task J: Finish the development branch

**No source plan section — this is the close-out.**

- [ ] **Step 1:** Run the verification skill before claiming done.

  Invoke: **superpowers:verification-before-completion**

  Required evidence to assert "Phase 1 done":
  - `git status` shows clean tree (only the `khanshoof_assets/` untracked carry-over).
  - `pytest` output line ending in `123 passed` (or the new total if frontend tasks added any backend tests — none planned).
  - Screenshot or written confirmation from Task I Steps 2–6 (mirrored sync verified by eye).

- [ ] **Step 2:** Push the branch.

  ```bash
  git push origin feature/multi-screen-walls-phase1
  ```

- [ ] **Step 3:** Invoke **superpowers:finishing-a-development-branch** to decide PR vs merge vs cleanup.

  This branch is based off `feature/security-hardening`, which is itself not yet PR'd to `main`. The finishing skill will surface that dependency. Likely the right move is:
  - Open a PR `feature/multi-screen-walls-phase1` → `feature/security-hardening` (so it's a small reviewable diff on top of security work), **or**
  - Merge `security-hardening` into `main` first, then PR walls into `main`.

  Defer the choice to the user — the skill walks through options.

- [ ] **Step 4:** Update memory.

  Edit `/home/ahmed/.claude/projects/-home-ahmed-signage/memory/project_walls_phase1_plan.md`:
  - Change "Status as of 2026-05-02" to reflect Tasks 7–15 done.
  - Note the PR URL (or merge SHA) once Step 3 produces one.
  - If the branch is merged, mark this memory as historical and consider replacing it with a Phase-2 (spanned-mode) pointer.

---

## Self-review

**Spec coverage:** Each remaining numbered task in the original plan (7–15) has a corresponding lettered task here (A–I) plus a Task J close-out. No gaps.

**Placeholder scan:** One legitimate "TBD" in Task H Step 1 — the nginx config file path genuinely depends on environment discovery (per the original plan's own Step 1 of Task 14, which asks the same investigation). Acceptable because Step 1 *is* the discovery step.

**Type consistency:** This plan only references endpoint paths and identifiers already shipped in commits `69f1c8e..10a5aea`. Cross-checked: `POST /walls`, `POST /walls/{id}/cells/{r}/{c}/pair-code`, `POST /walls/redeem`, `DELETE /walls/{id}/cells/{r}/{c}`, `GET /walls/{id}`, `GET /screens/{token}/content` (with `wall_id` + `wall_cell` fields), `wss://api.khanshoof.com/ws/walls/{token}` — all match the backend implementation as of `10a5aea`.

**Sequencing sanity:** A → B → C → D builds admin top-to-bottom (markup → list → editor → i18n). E → F → G builds player likewise (overlay → wall mode → i18n). H is independent infra. I is verification. J is close-out. No task depends on a later task.

---

## Done

Phase 1 (mirrored multi-screen walls) ships when Task J Step 3 produces a merged PR or a green merge commit. Phase 2 (spanned mode) is a separate spec and lives in `docs/superpowers/specs/2026-05-01-multi-screen-wall-sync-design.md` for the next round.
