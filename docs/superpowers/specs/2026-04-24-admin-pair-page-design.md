# Admin `/pair?code=…` Page — Design Spec

**Date:** 2026-04-24
**Status:** Ready for implementation planning
**Related:** Plan 1 (pairing backend — merged), Plan 2 (player QR UI — feature branch)

## Purpose

When a user scans the QR code shown on a TV by the player, their phone opens `https://app.sawwii.com/pair?code=XXXXX`. This page must let them pick which screen the TV belongs to and claim the code — completing the pairing handshake the TV is waiting on via `GET /screens/poll/{code}`.

Currently that URL falls back to the admin dashboard, which is confusing and dead-ends the flow. This spec builds the missing phone-first pairing page.

## Scope

**In scope**
- New `/pair` view inside the existing admin SPA (`frontend/`).
- Login gate: unauthenticated phones are sent through the existing login form, then resume the pair flow with the original code.
- Four-state controller: loading, form, success, error-inline.
- Form: editable code input + radio picker (existing screen dropdown OR create new screen inline) + claim button.
- Success state with "Pair another display" + "View dashboard" actions.
- Phone-first responsive layout, reusing the existing pastel Sawwii theme.

**Out of scope**
- Backend changes — every endpoint this view uses already exists (`POST /auth/login`, `GET /screens`, `POST /screens`, `POST /screens/claim`).
- Signup inside `/pair`. New users sign up on desktop; the pair link on the login form points to `/#signup` (losing the pair context, by design).
- Retiring the legacy `POST /screens/pair` + `screens.pair_code` column. That is Plan 4.
- Any changes to the player.

## Decisions (locked in during brainstorming)

| # | Decision | Chosen | Rejected alternatives |
|---|----------|--------|-----------------------|
| 1 | Unauth handling | **Login-only.** Show the normal login form; a "New here? Sign up →" link drops to `/#signup` (does not preserve pair state). | Inline signup tabs; instant orphan-org creation. |
| 2 | Screen selection | **Existing dropdown + inline "Create new screen".** One name input appears when create radio is selected; Claim does `POST /screens` then `POST /screens/claim`. | Existing-only; create-only. |
| 3 | Success state | **Inline success block** replacing the form: "Display paired ✓ · <name>" + [Pair another display] [View dashboard]. | Toast + redirect; full-screen static success. |
| 4 | View hosting | **Extend the existing SPA** (`frontend/app.js`, `index.html`, `styles.css`). New `#pair-view` panel, new `showPairView()` controller. | Separate `pair.html` entrypoint; hash-based `#pair/code` with nginx redirect. |

Baked-in UX defaults (not worth asking about):
- Code input is always editable (pre-filled from `?code=…` but user can correct mis-scans).
- Hitting `/pair` with no `?code=` shows the same form with an empty code field — manual entry is a first-class path.
- `sessionStorage` (not `localStorage`) holds the resume key, so it dies with the tab.

## Architecture

### Routing

The admin is a static SPA served by nginx with `try_files $uri $uri/ /index.html`, so `/pair` already loads the SPA — no nginx change. `boot()` branches on `location.pathname === "/pair"` before the existing dashboard / auth logic runs.

```
boot()
├─ if location.pathname === "/pair"
│   ├─ extract ?code= from location.search
│   ├─ if !authToken
│   │   ├─ sessionStorage.setItem("pair_resume", JSON.stringify({path:"/pair", code}))
│   │   └─ showAuthPanel()    // existing flow
│   └─ else
│       └─ showPairView(code)
└─ else (existing flow)
    ├─ if sessionStorage.getItem("pair_resume") after successful auth
    │   ├─ history.replaceState({}, "", `${resume.path}?code=${resume.code}`)
    │   ├─ sessionStorage.removeItem("pair_resume")
    │   └─ showPairView(resume.code)
    └─ else showDashboard()
```

The resume hook lives inside the existing `setAuth()` / post-login success path (current location in `app.js` around the `showDashboard()` call after a successful `/auth/login`).

### View structure

`#pair-view` is a new top-level sibling of `#auth-panel` and `#dashboard` in `index.html`. It contains four siblings:

```html
<section id="pair-view" class="pair-view hidden">
  <div id="pair-loading" class="hidden">…</div>
  <form id="pair-form" class="hidden">…</form>
  <div id="pair-success" class="hidden">…</div>
  <!-- error slot lives inside the form; no separate panel -->
</section>
```

`showPairView(code)` shows `#pair-view`, hides the other top-level panels, and transitions internal states. Internal state transitions use `.hidden` toggling, matching the codebase pattern.

### Controller (informal pseudocode)

```
showPairView(initialCode):
  hide #dashboard, #auth-panel
  show #pair-view
  show #pair-loading
  screens = await api("/screens")
  populate existing-screen dropdown
  pre-select null (no default) if >0 screens
  disable "existing" radio + select "create" by default if 0 screens
  code input.value = (initialCode || "").toUpperCase()
  hide #pair-loading, show #pair-form

onPairSubmit():
  disable Pair button
  if create radio:
    screen = await api("/screens", {method:"POST", body:{name: nameInput.value.trim()}})
    screenId = screen.id
  else:
    screenId = existingSelect.value
  try:
    await api("/screens/claim", {method:"POST", body:{code: codeInput.value, screen_id: screenId}})
    show #pair-success with screenName
    hide #pair-form
  catch HttpError e:
    showError(friendlyMessage(e))
    re-enable Pair button

"Pair another display":
  clear code input + name input
  default radio back to "existing" (or "create" if 0 screens)
  history.replaceState({}, "", "/pair")   // strip stale ?code= from URL
  hide #pair-success, show #pair-form

"View dashboard":
  history.pushState({}, "", "/")
  hide #pair-view
  showDashboard(); bootData()
```

### Data flow

All calls go through the existing `api()` helper, which:
- Injects `Authorization: Bearer <authToken>`
- Throws a typed error with `status` + parsed body on non-2xx
- Triggers `handleAuthFailure()` on 401

No new global state. `showPairView` reads `state.screens` if already populated (the existing dashboard prefetch may run first in some flows); otherwise makes its own `GET /screens`.

### Styling

Matches the existing pastel Sawwii palette (`--cream`, `--peach`, `--peach-deep`, `--plum`, IBM Plex Serif + Sans/Mono). Phone-first single-column, generous touch targets (≥ 44 px), `clamp()`-based sizing identical in spirit to the player pairing view. The code input uses `font-family: var(--mono)`, letter-spacing, and centered alignment so the 5-char code reads like an OTP field.

## Error handling

All claim/create errors show a single inline error node under the Pair button. The form stays interactive so the user can change the code or the screen choice and retry without navigating away.

| Backend response | User-facing message |
|---|---|
| `404 Unknown pairing code` | "That code isn't recognised. Check the TV screen and try again." |
| `400 Pairing code expired. Ask the display to refresh.` | "Code expired. Refresh the TV to get a new one." |
| `409 Pairing code already claimed by another screen` | "That code's been used. Refresh the TV to get a new one." |
| `400 pair_code is already bound to a different screen` | "This code belongs to a different display. Refresh the TV to get a new one." |
| `402 Plan screen limit reached` (on create) | "You've hit your plan's screen limit. Upgrade to add more." |
| `403 Forbidden` (viewer role) | "Your account doesn't have permission to pair displays." |
| Anything else | "Something went wrong — please try again." |

Network errors (fetch throws): "Can't reach server. Please try again."

Idempotency: the backend's `/screens/claim` is idempotent for the same `(caller, screen)` pair — double-tapping Pair never corrupts state.

Cross-tab logout mid-pair: the existing `handleAuthFailure()` helper clears `authToken` and bounces to the auth panel. `pair_resume` is NOT re-stashed in this case — the user must re-scan if they want to continue.

## Testing

**Backend regression:** existing pytest suite (38 tests) must remain green — no backend changes. Run `docker-compose run --rm backend pytest` as the baseline.

**Frontend smoke matrix** (manual, browser-driven, run once before merge):

| # | Scenario | Steps | Expected |
|---|----------|-------|----------|
| 1 | Fresh pair, not logged in | Incognito → scan TV QR → login prompt → login → `/pair` resumes with code pre-filled | Pair succeeds; player swaps to content within 3 s |
| 2 | Pair with existing screen | Logged in, `/pair?code=X`, pick existing screen | Claim succeeds; player swaps |
| 3 | Pair with new screen | Logged in, `/pair?code=X`, pick "Create new", name it | New screen row created + claimed; player swaps |
| 4 | Manual code entry | `/pair` (no query), type 5-char code from TV | Pair succeeds |
| 5 | Expired code | Wait > 10 min on TV, then Pair | Inline error "Code expired…"; form stays usable |
| 6 | Wrong code | Type a garbage code | Inline error "That code isn't recognised…" |
| 7 | Plan limit | Starter trial with 3 screens, try to create 4th | Inline error "You've hit your plan's screen limit." |
| 8 | Success → pair another | After success, tap "Pair another display" | Form re-renders, code field empty, existing radio default |
| 9 | Success → view dashboard | After success, tap "View dashboard" | `/` loads normal dashboard |

Accessibility spot-check: form has labelled inputs, Pair button disabled state is exposed via `aria-disabled`, success heading uses a real `<h1>` (like the player pairing view), inline error uses `role="alert"`.

## Out-of-scope / explicit non-goals

- Signup from `/pair` (users sign up on desktop).
- QR-generation on the admin side (player owns that).
- Retiring `POST /screens/pair` + `screens.pair_code` — reserved for Plan 4.
- Adding a dedicated mobile UI for the main dashboard. Only the `/pair` view is phone-first in this spec.
- Pre-validating the code client-side (e.g., calling `/screens/poll/{code}` before submit). Backend is the source of truth; a pre-validate step adds latency and doubles the error surface.

## Files touched

```
frontend/
├── index.html   # ADD #pair-view section (siblings: loading/form/success)
├── styles.css   # ADD .pair-view* rules (phone-first, reuses --cream/--peach/--plum)
└── app.js       # ADD /pair routing branch in boot(), showPairView(), pair-form submit,
                 #     success transitions, pair_resume session replay, error-surface helper
```

No backend, player, or landing changes.
