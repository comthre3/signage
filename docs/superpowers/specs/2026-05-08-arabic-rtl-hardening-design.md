# Arabic / RTL Hardening — Design

**Status:** approved 2026-05-08
**Phase:** 2.5b — Arabic / RTL polish (first of three queued initiatives: Arabic → Security → Payment gateway)
**Branch base:** `main` (new branch `feature/arabic-rtl-hardening`)

---

## Section 0 — Why

Phase 1 shipped EN+AR bilingual with MSA admin/system + Kuwaiti dialect on landing playful surfaces. An audit on 2026-05-08 found 21 gaps across coverage, RTL, and locale handling. This spec scopes a focused fix-pass — the highest-value items (P0 + most P1) — into a single implementation plan.

**Out-of-scope** items are listed in Section 9 with reasons. Plan-name translation (Starter/Growth/etc.) is deferred because it's a branding decision, not an engineering one.

Goals:
- Eliminate user-visible English in the admin when the user is in Arabic mode (browser `confirm()` dialogs, hardcoded strings).
- Fix one RTL layout bug (landing mobile nav dropdown).
- Make text-direction auto-detect work in form inputs.
- Give the player's pre-pair screen a language toggle.

Non-goals:
- Arabic-Indic digit rendering (Kuwait commerce uses Latin digits).
- `Intl.DateTimeFormat` upgrade (no user-facing dates today).
- Translation-quality / dead-key checks in `scripts/check_i18n.py`.
- Zone-editor handle positioning (P2 polish, not user-blocking).

---

## Section 1 — Custom confirm-modal

**Problem:** Browser `confirm()` dialogs always render in the browser's UI language regardless of our app's locale. Audit found 7+ confirm-sites in `frontend/app.js` (delete site / playlist / user / group / screen / playlist item / wall, plus media-picker remove and various "are you sure"s). All show English in AR mode.

**Solution:** A small reusable function `confirmDialog({ title, message, confirmLabel, danger })` that returns `Promise<boolean>` (true = confirmed, false = cancelled). Built on the same `.modal` / `.modal-card` styles already used by the mode-change modal and media picker.

### Interface

```js
window.confirmDialog = function ({ title, message, confirmLabel, danger = false }) → Promise<boolean>
```

Resolves `true` on confirm, `false` on cancel / Esc / backdrop click.

### UX shape

```
┌──────────────────────────────────────┐
│ {title}                          ✕   │
├──────────────────────────────────────┤
│ {message}                            │
├──────────────────────────────────────┤
│            [Cancel]  [{confirmLabel}]│
└──────────────────────────────────────┘
```

Single-instance: opening while one is mounted resolves the *new* call with `false` (defensive).

### Migration of existing call sites

Each existing `confirm("...")` becomes:

```js
if (!(await confirmDialog({
  title:   Khan.t("confirm.delete_X_title", "Delete X"),
  message: Khan.t("confirm.delete_X_body", "Delete X?"),
  confirmLabel: Khan.t("confirm.delete_label", "Delete"),
  danger:  true,
}))) return;
```

Sites to migrate (from the audit; verify by `grep -n "confirm(" frontend/app.js`):
- Delete site
- Delete playlist
- Delete media (if present)
- Delete user
- Delete group
- Delete screen
- Delete playlist item
- Delete wall
- Unpair cell
- Delete canvas item (already wired to `confirm()` from Phase 2)
- Mode-change confirmation already uses a typed-name modal — leave it alone.

A grep at implementation time will produce the canonical list. Each site gains one or two i18n keys (title + body).

### i18n keys

One generic + per-action specific:

```
"confirm.cancel": "Cancel" / "إلغاء"
"confirm.delete_label": "Delete" / "حذف"
"confirm.title_delete": "Delete" / "حذف" (generic title fallback)
"confirm.delete_site_title": "Delete site"
"confirm.delete_site_body": "Delete site \"{name}\"? Screens in this site become unassigned."
... (per-entity title + body)
```

Estimated new keys: ~24 (12 sites × 2 keys, minus shared cancel/delete labels). Each gets EN + AR.

---

## Section 2 — Hardcoded English string extraction

**Problem:** Audit found 10–12 spots in `frontend/app.js` with literal English in DOM/innerHTML/textContent that aren't going through `Khan.t()`. Examples (line numbers approximate; verify at implementation):

- `app.js:348` — "Admin access required to manage users."
- `app.js:429` — "Groups" (heading.textContent)
- `app.js:537–538` — "Previewing: …", "Unassigned", "expires"
- `app.js:601` — `<div class="zone-title">New Zone</div>`
- `app.js:262, 302, 720` — `<option>` placeholders in select-dropdowns
- `app.js:~1210` — "Welcome to Khanshoof, {name}!" trial-active toast
- `app.js:~1215` — "{n} file(s) uploaded" toast
- Various error-fallback strings: "Login failed.", "Couldn't add screen.", "Couldn't send code." — these appear when API calls fail with no useful server message

**Solution:** Mechanical extraction. Each hardcoded string gets:
1. A new i18n key in `frontend/i18n/en.json` and `frontend/i18n/ar.json`.
2. The literal replaced with `Khan.t("key", "english fallback")` in `app.js`.
3. For `<option>` placeholders in dropdowns rendered by JS, use `Khan.t()` directly when building the option list. (Static placeholders in `index.html` already use `data-i18n` correctly.)

### i18n key inventory (estimate ~12 keys, EN + AR)

```
"users.admin_required": "Admin access required to manage users."
"users.groups_heading": "Groups"
"screens.preview_meta_label": "Previewing"
"screens.preview_meta_unassigned": "Unassigned"
"screens.preview_meta_expires": "expires"
"screens.zone_default_name": "New Zone"
"screens.site_placeholder": "Site"
"playlists.placeholder": "Select playlist"
"toast.signup_welcome": "Welcome to Khanshoof, {name}! Your 5-day trial is active."
"toast.files_uploaded_n": "{n} file(s) uploaded"
"error.login_failed": "Login failed."
"error.screen_add_failed": "Couldn't add screen."
"error.code_send_failed": "Couldn't send code."
```

(More may surface during implementation; the plan's grep step will catch them.)

The error-fallback pattern: keep using `err.message || Khan.t("error.foo_failed", "fallback")` so server-provided messages still wins when present (server messages are already localized via `error.*` codes per Phase 1's structured-error refactor).

---

## Section 3 — Resolution dropdown translation

**Problem:** `frontend/index.html:244–247` has `<option>` elements with hardcoded English labels:

```html
<option value="1920x1080">FHD (1920×1080)</option>
<option value="2560x1440">2K (2560×1440)</option>
<option value="3840x2160">4K UHD (3840×2160)</option>
<option value="4096x2160">4K DCI (4096×2160)</option>
```

These don't translate.

**Solution:** Add `data-i18n` to each `<option>` and four new keys:

```
"screens.resolution_fhd": "FHD (1920×1080)"
"screens.resolution_2k": "2K (2560×1440)"
"screens.resolution_4k_uhd": "4K UHD (3840×2160)"
"screens.resolution_4k_dci": "4K DCI (4096×2160)"
```

In Arabic, the labels become e.g. `"FHD (1920×1080)"` (kept in Latin form — these are technical model names, not natural-language phrases). MSA spec is "use English technical names verbatim" — common practice for display-resolution shorthand. Same convention used for `KNET`, `KWD`, `PDF`.

---

## Section 4 — Lang-toggle aria-label

**Problem:** The `#lang-toggle` button in `frontend/index.html:34` and `landing/index.html:32` has hardcoded `aria-label="Switch language"`. Screen readers announce the wrong language string when AR is active.

**Solution:** Replace with `data-i18n-aria-label="nav.lang_toggle_aria"`. The i18n boot already handles this attribute (verified in `frontend/i18n.js:33`, `landing/i18n.js:33`, `player/i18n.js:33`).

New key (single, shared):
```
"nav.lang_toggle_aria": "Switch language" / "تبديل اللغة"
```

---

## Section 5 — Player pairing-screen language toggle

**Problem:** The player's pre-pair screen (shown to end-users at TVs / display devices) has no language toggle. Once a screen is paired, it inherits the org's locale, but the pairing screen itself is English-only.

**Solution:** Add a small language toggle button at a corner of the pairing screen (top-right in LTR / top-left in RTL — `inset-inline-end`). Same toggle pattern as admin: text label "EN" / "عربي" (the *other* language, so Arabic users see "EN" to switch back). On click: flips locale, persists in the existing `khanshoof_lang` cookie (same name as admin/landing — they share the `.khanshoof.com` domain), re-runs the i18n boot.

The cookie persistence already works for admin / landing via `Khan.setLocale(locale)`. The **player's `Khan` does NOT currently expose `setLocale`** (verified: `player/i18n.js` returns `{ loadLocale, t, applyTranslations, detectInitialLocale, currentLocale }` — no `setLocale`). So this task includes a small extension to `player/i18n.js`:

- Add a `setLocale(locale)` function that writes the `khanshoof_lang` cookie (mirror the admin/landing implementation: respect `.khanshoof.com` domain in prod).
- Export it on `window.Khan`.

### Files affected
- `player/i18n.js` — add `setCookie` + `setLocale` (mirror admin/landing lines 36–47), export on `window.Khan`.
- `player/index.html` — add the button markup near the pairing section, with `data-i18n` and `data-i18n-aria-label`.
- `player/player.js` — wire the click handler: read the *other* locale (`Khan.currentLocale() === "ar" ? "en" : "ar"`), call `Khan.setLocale(other)`, then `await Khan.loadLocale(other); Khan.applyTranslations();`.
- `player/styles.css` — small absolute-positioned button using `inset-inline-end` and `inset-block-start`.

### i18n keys
- `lang.toggle_label` — currently `"EN"` in `frontend/i18n/ar.json` and `"عربي"` in `frontend/i18n/en.json` (the *other* language, so the button shows what you'd switch *to*). Verify or add the same key to `player/i18n/{en,ar}.json`.
- `nav.lang_toggle_aria` — already added in Section 4; reuse here.

---

## Section 6 — Landing mobile nav dropdown — physical → logical CSS

**Problem:** `landing/styles.css:271`:

```css
.nav-links {
  /* mobile dropdown variant */
  top: 100%;
  left: 0;
  right: 0;
  ...
}
```

In RTL the dropdown anchors correctly because `left:0; right:0;` happens to be direction-agnostic when both are set. **But** if the parent has any padding-inline that's asymmetric, the dropdown still doesn't align cleanly. The fix is canonical hygiene either way.

**Solution:** Replace with logical properties:

```css
.nav-links {
  inset-block-start: 100%;
  inset-inline: 0;
  ...
}
```

(The non-mobile variant of `.nav-links` is already direction-agnostic and needs no change.)

---

## Section 7 — Form inputs `dir="auto"`

**Problem:** When a user types English text into an Arabic-locale page (or vice versa), input direction doesn't auto-flip per character. Effects: cursor jumps, mixed content reads awkward.

**Solution:** Add `dir="auto"` to every user-text-input field across admin, landing, and player. `dir="auto"` is the HTML attribute that tells the browser to detect direction from the *first strong character* in the typed content.

Apply to:
- All `<input type="text">`, `<input type="email">`, `<input type="search">`, `<input type="tel">`, `<input type="url">`, `<input type="number">`, `<textarea>`.
- **Skip** `<input type="password">` (no display direction concern, and obscured chars don't have a strong direction).
- **Skip** OTP / pair-code inputs that should always be LTR (they accept ASCII codes only). For these, set `dir="ltr"` explicitly.

Concrete sites: grep `<input ` and `<textarea ` across the three index.html files. Each gets the appropriate `dir` attribute.

---

## Section 8 — Tests + smoke

**Backend:** No backend-testable changes in this spec. Test count stays at 150.

**Frontend:** No automated tests in this repo. Manual smoke (post-deploy):

1. **Confirm-modal AR sanity:**
   - Switch admin to Arabic.
   - Click delete on any site, playlist, user, group, screen, playlist item, wall.
   - Each shows the new modal with title/body in Arabic, Cancel/Delete buttons in Arabic.
   - Esc / Cancel closes without action; Delete confirms.

2. **Hardcoded-string extraction sanity:**
   - In Arabic mode, navigate to: Users tab (admin-required heading), Screens tab (resolution dropdown), Sites tab (preview meta), Playlists tab (select-placeholder).
   - Confirm zero English strings appear in the visible UI.

3. **Player pairing-screen toggle:**
   - Open `play.khanshoof.com` in a fresh browser (no `khan_locale` set).
   - Click the language toggle button. Pairing screen text flips to Arabic.
   - Reload — language persists.
   - Click again to flip back to English.

4. **Landing mobile nav (RTL):**
   - Resize browser to mobile width (≤640px). Switch to Arabic.
   - Click the hamburger / nav toggle. Dropdown should anchor flush to both edges, no shift.

5. **Form input `dir="auto"`:**
   - In Arabic admin, focus a text input. Type "hello" (Latin) — cursor + content stays LTR.
   - Type "مرحبا" (Arabic) — cursor + content flips to RTL.
   - OTP input: type a 6-digit code. Stays LTR regardless of locale.

6. **i18n parity:**
   - Run `python3 scripts/check_i18n.py` — must pass.
   - Run `python3 -c "import json; en=json.load(open('frontend/i18n/en.json')); ar=json.load(open('frontend/i18n/ar.json')); print(len(en), len(ar), set(en)==set(ar))"` — counts equal, parity true.

---

## Section 9 — Out of scope (deferred)

| Item | Why deferred |
|---|---|
| Plan-name translation (Starter/Growth/Business/Pro/Enterprise) | Branding decision, not engineering. Discuss separately. |
| Arabic-Indic digit rendering (٠–٩) | Kuwait commerce uses Latin digits; no user feedback requesting this. |
| `Intl.DateTimeFormat` upgrade | No user-facing dates rendered today. |
| Translation-quality automation (`check_i18n.py` deep audit) | Separate tooling concern; the existing parity gate is sufficient. |
| Zone-editor handle pixel-positioning (P2 polish) | Cosmetic, not user-blocking. |
| Plural-form handling in Arabic (`{n}` strings) | Arabic plural rules are nuanced (dual + specific count categories); current single-form keys are accepted-imperfect. Revisit if a user complains. |
| Walls feature interpolation comments for translators | We're the only translator. |

---

## Section 10 — Spec self-review

**Placeholder scan:** None. Every section has concrete file paths, key names, and behaviors.

**Internal consistency:**
- Section 1's `confirm.cancel` and `confirm.delete_label` are reused across all confirm-sites.
- Section 4's `nav.lang_toggle_aria` is shared across admin and landing (single key, reused via `data-i18n-aria-label` on both).
- Section 7's `dir="auto"` exemption list (passwords, OTP/pair-code) is consistent with Section 5 (player pairing screen).

**Scope check:** Seven discrete work items, ~36–40 new i18n keys, one new JS helper (`confirmDialog`), one CSS rule fix, one new player UI element. Single implementation plan, ~6–8 tasks.

**Ambiguity check:**
- "Resolution labels stay in Latin form in Arabic" — explicit in Section 3 with rationale (technical names, common practice).
- `dir="auto"` exemptions — explicit list in Section 7.
- Confirm-modal single-instance behavior — explicit in Section 1 (second open resolves with `false`).

No ambiguities found that would change behavior.

---

## Section 11 — Done definition

When this ships green:
- Zero English strings visible in the admin when locale = Arabic, except where Latin form is intentional (KNET, KWD, PDF, FHD, 2K, 4K, brand strings).
- Zero browser `confirm()` calls remain in `frontend/app.js`. All replaced by `confirmDialog`.
- `python3 scripts/check_i18n.py` passes.
- Player pairing screen is bilingual.
- Landing mobile nav dropdown is logical-CSS.
- Form inputs respect typed-content direction.
- All 6 manual-smoke checkpoints pass.

Phase 2.5c candidates (next initiative — security):
1. Account lockout after N failed auth attempts.
2. Audit log for sensitive admin actions.
3. Password policy strengthening + breach-list check.
4. 2FA / OTP for admin login.
5. Session-revocation UI.

(Listed here so they're not lost; will be the brainstorming starting point for the security initiative after this one ships.)
