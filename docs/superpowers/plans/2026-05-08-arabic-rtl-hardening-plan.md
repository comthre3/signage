# Arabic / RTL Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace browser `confirm()` with a localized custom modal, extract hardcoded English strings to i18n keys, fix one RTL CSS bug, add a language toggle to the player pairing screen, and apply `dir="auto"` to text inputs.

**Architecture:** Pure frontend / i18n work. One new helper (`confirmDialog`) added to `frontend/app.js`. ~36–40 new EN+AR i18n keys. Small extension to `player/i18n.js` (add `setLocale`). One CSS bug fix in `landing/styles.css`. Markup updates across `frontend/index.html`, `landing/index.html`, `player/index.html`. No backend changes. No schema changes. No new backend tests.

**Tech Stack:** Vanilla JS (IIFE / window globals), CSS logical properties, JSON i18n via `Khan.t()` and `data-i18n` / `data-i18n-aria-label` attributes.

**Branch:** `feature/arabic-rtl-hardening` — already created at commit `c9ceb51` (spec). Stack chain ends at `main`.

**Spec:** `docs/superpowers/specs/2026-05-08-arabic-rtl-hardening-design.md`.

**Test budget:** 150 backend tests (unchanged). i18n parity gate via `scripts/check_i18n.py`.

---

## File map

- **Modify:** `frontend/app.js`
  - **Add:** `confirmDialog(...)` helper function (window-global, near other globals like `toast`).
  - **Migrate:** the 7 `confirm()` call sites (lines 248, 288, 333, 381, 443, 521, 939) to use `confirmDialog`.
  - **Migrate:** the 3 `confirm()` call sites already wrapped with `Khan.t()` (lines 2370, 2529, 2680) for parity.
  - **Replace** ~12 hardcoded English string literals with `Khan.t(...)` calls (lines 348, 429, 473, 537, 601, 720, 1039, 1111, 1194, 1266, plus options dropdown placeholders rendered in JS).
- **Modify:** `frontend/index.html`
  - Resolution dropdown (lines 244–247): add `data-i18n` to each `<option>`.
  - Lang-toggle button (line 34): replace hardcoded `aria-label="Switch language"` with `data-i18n-aria-label="nav.lang_toggle_aria"`.
  - All user-text `<input>` elements: add `dir="auto"` (skip password / OTP / pair-code per spec).
- **Modify:** `landing/index.html`
  - Lang-toggle button (line 32): same `data-i18n-aria-label` swap.
  - User-text inputs: `dir="auto"`.
- **Modify:** `landing/styles.css`
  - `.nav-links` mobile dropdown rule (~line 271): replace `left: 0; right: 0;` with `inset-inline: 0;`.
- **Modify:** `player/index.html`
  - Add language-toggle button markup near the pairing section.
  - User-text inputs (admin-code input): `dir="ltr"` explicit.
- **Modify:** `player/player.js`
  - Wire the language-toggle click handler.
- **Modify:** `player/i18n.js`
  - Add `setCookie` helper + `setLocale(locale)` function (mirror admin/landing). Export on `window.Khan`.
- **Modify:** `player/styles.css`
  - Position the language-toggle button.
- **Modify:** `frontend/i18n/en.json` and `frontend/i18n/ar.json`
  - ~36–40 new keys.
- **Modify:** `player/i18n/en.json` and `player/i18n/ar.json`
  - Add `lang.toggle_label` (if not present) and `nav.lang_toggle_aria`.

---

## Task 1: `confirmDialog` helper + migrate every `confirm()` site

**Files:**
- Modify: `frontend/app.js` (add helper near other window-globals like `toast`; migrate 10 call sites).
- Modify: `frontend/i18n/en.json` and `frontend/i18n/ar.json` (~26 new keys).

- [ ] **Step 1: Add the helper function**

In `frontend/app.js`, find where `toast(...)` is defined. Add `confirmDialog` immediately after it (top-level, exposed on `window`):

```javascript
// Localized replacement for window.confirm. Returns Promise<boolean>.
window.confirmDialog = function ({ title, message, confirmLabel, danger = false }) {
  return new Promise((resolve) => {
    if (document.querySelector(".confirm-dialog-modal")) {
      // Defensive: never mount a second one.
      resolve(false);
      return;
    }
    const overlay = document.createElement("div");
    overlay.className = "modal confirm-dialog-modal";
    overlay.innerHTML = `
      <div class="modal-card confirm-dialog-card">
        <div class="confirm-dialog-header">
          <h3>${title || ""}</h3>
          <button class="confirm-dialog-close btn-ghost" aria-label="Close">✕</button>
        </div>
        <div class="confirm-dialog-body">
          <p>${(message || "").replace(/</g, "&lt;")}</p>
        </div>
        <div class="confirm-dialog-actions">
          <button class="btn btn-ghost confirm-dialog-cancel">${
            Khan.t("confirm.cancel", "Cancel")}</button>
          <button class="btn ${danger ? "btn-danger" : "btn-primary"} confirm-dialog-confirm">${
            confirmLabel || Khan.t("confirm.delete_label", "Delete")}</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    let settled = false;
    function settle(value) {
      if (settled) return;
      settled = true;
      document.removeEventListener("keydown", onKeyDown);
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      resolve(value);
    }
    function onKeyDown(e) {
      if (e.key === "Escape") settle(false);
      else if (e.key === "Enter") settle(true);
    }

    overlay.addEventListener("click", (e) => { if (e.target === overlay) settle(false); });
    overlay.querySelector(".confirm-dialog-close").addEventListener("click", () => settle(false));
    overlay.querySelector(".confirm-dialog-cancel").addEventListener("click", () => settle(false));
    overlay.querySelector(".confirm-dialog-confirm").addEventListener("click", () => settle(true));

    document.addEventListener("keydown", onKeyDown);

    // Focus the cancel button by default (safer for destructive actions).
    setTimeout(() => overlay.querySelector(".confirm-dialog-cancel").focus(), 0);
  });
};
```

- [ ] **Step 2: Append CSS for the modal**

In `frontend/styles.css`, append at the end:

```css
/* ── Confirm Dialog ─────────────────────────────────────────── */

.confirm-dialog-modal .modal-card.confirm-dialog-card {
  width: min(440px, 92vw);
  padding: 0;
  display: flex;
  flex-direction: column;
}

.confirm-dialog-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border, #e9ddc6);
}

.confirm-dialog-header h3 {
  margin: 0;
  font-size: 16px;
}

.confirm-dialog-close {
  background: transparent;
  border: 0;
  font-size: 18px;
  cursor: pointer;
}

.confirm-dialog-body {
  padding: 14px 18px;
  font-size: 14px;
  line-height: 1.4;
}

.confirm-dialog-body p {
  margin: 0;
}

.confirm-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 18px;
  border-top: 1px solid var(--border, #e9ddc6);
}
```

- [ ] **Step 3: Add the i18n keys to `frontend/i18n/en.json`**

Append before the closing `}` (with comma after current last key):

```json
  "confirm.cancel": "Cancel",
  "confirm.delete_label": "Delete",
  "confirm.delete_site_title": "Delete site",
  "confirm.delete_site_body": "Delete site \"{name}\"? Screens in this site become unassigned.",
  "confirm.delete_playlist_title": "Delete playlist",
  "confirm.delete_playlist_body": "Delete playlist \"{name}\"?",
  "confirm.delete_media_title": "Delete media",
  "confirm.delete_media_body": "Delete \"{name}\"?",
  "confirm.delete_user_title": "Delete user",
  "confirm.delete_user_body": "Delete user \"{name}\"?",
  "confirm.delete_group_title": "Delete group",
  "confirm.delete_group_body": "Delete group \"{name}\"?",
  "confirm.delete_screen_title": "Delete screen",
  "confirm.delete_screen_body": "Delete screen \"{name}\"?",
  "confirm.remove_item_title": "Remove item",
  "confirm.remove_item_body": "Remove this item from the playlist?"
```

- [ ] **Step 4: Add the same keys to `frontend/i18n/ar.json`**

```json
  "confirm.cancel": "إلغاء",
  "confirm.delete_label": "حذف",
  "confirm.delete_site_title": "حذف الموقع",
  "confirm.delete_site_body": "حذف الموقع \"{name}\"؟ ستصبح الشاشات في هذا الموقع غير مُعيَّنة.",
  "confirm.delete_playlist_title": "حذف قائمة التشغيل",
  "confirm.delete_playlist_body": "حذف قائمة التشغيل \"{name}\"؟",
  "confirm.delete_media_title": "حذف الوسيط",
  "confirm.delete_media_body": "حذف \"{name}\"؟",
  "confirm.delete_user_title": "حذف المستخدم",
  "confirm.delete_user_body": "حذف المستخدم \"{name}\"؟",
  "confirm.delete_group_title": "حذف المجموعة",
  "confirm.delete_group_body": "حذف المجموعة \"{name}\"؟",
  "confirm.delete_screen_title": "حذف الشاشة",
  "confirm.delete_screen_body": "حذف الشاشة \"{name}\"؟",
  "confirm.remove_item_title": "إزالة العنصر",
  "confirm.remove_item_body": "إزالة هذا العنصر من قائمة التشغيل؟"
```

- [ ] **Step 5: Migrate the 7 raw-English `confirm()` sites**

In `frontend/app.js`, do EACH of these replacements:

**Line 248** (delete site):
```javascript
      if (!confirm(`Delete site "${site.name}"?`)) return;
```
→
```javascript
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_site_title", "Delete site"),
        message: Khan.t("confirm.delete_site_body", "Delete site \"{name}\"? Screens in this site become unassigned.").replace("{name}", site.name),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
```

**Line 288** (delete playlist):
```javascript
      if (!confirm(`Delete playlist "${playlist.name}"?`)) return;
```
→
```javascript
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_playlist_title", "Delete playlist"),
        message: Khan.t("confirm.delete_playlist_body", "Delete playlist \"{name}\"?").replace("{name}", playlist.name),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
```

**Line 333** (delete media):
```javascript
      if (!confirm(`Delete "${item.name}"?`)) return;
```
→
```javascript
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_media_title", "Delete media"),
        message: Khan.t("confirm.delete_media_body", "Delete \"{name}\"?").replace("{name}", item.name),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
```

**Line 381** (delete user):
```javascript
      if (!confirm(`Delete user "${user.username}"?`)) return;
```
→
```javascript
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_user_title", "Delete user"),
        message: Khan.t("confirm.delete_user_body", "Delete user \"{name}\"?").replace("{name}", user.username),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
```

**Line 443** (delete group):
```javascript
      if (!confirm(`Delete group "${group.name}"?`)) return;
```
→
```javascript
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_group_title", "Delete group"),
        message: Khan.t("confirm.delete_group_body", "Delete group \"{name}\"?").replace("{name}", group.name),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
```

**Line 521** (delete screen):
```javascript
      if (!confirm(`Delete screen "${screen.name}"?`)) return;
```
→
```javascript
      if (!(await confirmDialog({
        title:   Khan.t("confirm.delete_screen_title", "Delete screen"),
        message: Khan.t("confirm.delete_screen_body", "Delete screen \"{name}\"?").replace("{name}", screen.name),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
```

**Line 939** (remove playlist item):
```javascript
      if (!confirm("Remove this item?")) return;
```
→
```javascript
      if (!(await confirmDialog({
        title:   Khan.t("confirm.remove_item_title", "Remove item"),
        message: Khan.t("confirm.remove_item_body", "Remove this item from the playlist?"),
        confirmLabel: Khan.t("confirm.delete_label", "Delete"),
        danger:  true,
      }))) return;
```

If any of these click handlers is not already `async`, change the surrounding function to `async (...)` so `await` works. Most click listeners in this file are already async.

- [ ] **Step 6: Migrate the 3 wall-related `confirm()` sites for parity**

Lines 2370, 2529, 2680 already wrap in `Khan.t` but use `confirm()` (which is still browser-language). Migrate them to `confirmDialog` for full consistency:

**Line 2370** (delete wall — find the surrounding function `deleteWall(id)`):
```javascript
    if (!confirm(Khan.t("walls.confirm_delete", "Delete this wall? Paired screens will revert to standalone."))) return;
```
→
```javascript
    if (!(await confirmDialog({
      title:   Khan.t("walls.confirm_delete_title", "Delete wall"),
      message: Khan.t("walls.confirm_delete", "Delete this wall? Paired screens will revert to standalone."),
      confirmLabel: Khan.t("confirm.delete_label", "Delete"),
      danger:  true,
    }))) return;
```

**Line 2529** (unpair cell):
```javascript
    if (!confirm(Khan.t("walls.confirm_unpair", "Unpair this cell?"))) return;
```
→
```javascript
    if (!(await confirmDialog({
      title:   Khan.t("walls.confirm_unpair_title", "Unpair cell"),
      message: Khan.t("walls.confirm_unpair", "Unpair this cell?"),
      confirmLabel: Khan.t("walls.unpair_label", "Unpair"),
      danger:  true,
    }))) return;
```

**Line 2680** (delete canvas item):
```javascript
    if (!confirm(Khan.t("walls.canvas_confirm_delete", "Delete this item?"))) return;
```
→
```javascript
    if (!(await confirmDialog({
      title:   Khan.t("walls.canvas_confirm_delete_title", "Delete item"),
      message: Khan.t("walls.canvas_confirm_delete", "Delete this item?"),
      confirmLabel: Khan.t("confirm.delete_label", "Delete"),
      danger:  true,
    }))) return;
```

Add 3 new title keys + 1 new "unpair_label" to en.json + ar.json:

EN:
```json
  "walls.confirm_delete_title": "Delete wall",
  "walls.confirm_unpair_title": "Unpair cell",
  "walls.canvas_confirm_delete_title": "Delete item",
  "walls.unpair_label": "Unpair"
```
AR:
```json
  "walls.confirm_delete_title": "حذف الجدار",
  "walls.confirm_unpair_title": "فصل الخلية",
  "walls.canvas_confirm_delete_title": "حذف العنصر",
  "walls.unpair_label": "فصل"
```

- [ ] **Step 7: Verify zero raw `confirm(` calls remain**

```bash
grep -n "[^.]\bconfirm(" frontend/app.js
```

Expected: zero matches. (Excludes `Khan.t` and other dotted calls; matches only direct `confirm(`.)

- [ ] **Step 8: Verify JS parses + i18n parity**

```bash
node --check frontend/app.js && echo "JS OK"
python3 -c "import json; en=json.load(open('frontend/i18n/en.json')); ar=json.load(open('frontend/i18n/ar.json')); print('en:', len(en), 'ar:', len(ar), 'parity:', set(en)==set(ar))"
python3 scripts/check_i18n.py
```

Expected: `JS OK`, `en: 278 ar: 278 parity: True` (258 baseline + 20 new), `i18n OK across frontend, landing, player`.

- [ ] **Step 9: Commit**

```bash
git add frontend/app.js frontend/styles.css frontend/i18n/en.json frontend/i18n/ar.json
git commit -m "$(cat <<'EOF'
feat(arabic-rtl): confirmDialog modal replaces browser confirm()

Browser confirm() always renders in the browser's UI language, so
admin in Arabic mode showed English delete dialogs. New
window.confirmDialog({title,message,confirmLabel,danger}) returns
Promise<boolean> and uses the same .modal styling family as the
mode-change modal and media picker. All 10 confirm() sites in
frontend/app.js migrated (7 raw + 3 walls-related). 20 new i18n
keys EN + AR (MSA).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Extract hardcoded English strings in `frontend/app.js`

**Files:**
- Modify: `frontend/app.js` (~10 line-edits across the file).
- Modify: `frontend/i18n/en.json` and `frontend/i18n/ar.json` (~12 new keys).

- [ ] **Step 1: Add new i18n keys to `frontend/i18n/en.json`**

Append:

```json
  "users.admin_required": "Admin access required to manage users.",
  "users.groups_heading": "Groups",
  "screens.preview_meta_label": "Previewing",
  "screens.preview_meta_unassigned": "Unassigned",
  "screens.preview_meta_expires": "expires",
  "screens.zone_default_name": "New Zone",
  "screens.access_unassigned_option": "Unassigned",
  "screens.site_unassigned_label": "Unassigned",
  "toast.signup_welcome": "Welcome to Khanshoof, {name}! Your 5-day trial is active.",
  "toast.files_uploaded_n": "{n} file(s) uploaded.",
  "error.login_failed": "Login failed.",
  "error.code_send_failed": "Couldn't send code."
```

- [ ] **Step 2: Add the same 12 keys to `frontend/i18n/ar.json`**

```json
  "users.admin_required": "صلاحية المدير مطلوبة لإدارة المستخدمين.",
  "users.groups_heading": "المجموعات",
  "screens.preview_meta_label": "جارٍ معاينة",
  "screens.preview_meta_unassigned": "غير مُعيَّنة",
  "screens.preview_meta_expires": "تنتهي",
  "screens.zone_default_name": "منطقة جديدة",
  "screens.access_unassigned_option": "غير مُعيَّن",
  "screens.site_unassigned_label": "غير مُعيَّن",
  "toast.signup_welcome": "مرحباً بك في خنشوف، {name}! تجربتك المجانية لمدة 5 أيام نشطة الآن.",
  "toast.files_uploaded_n": "تم رفع {n} ملفاً.",
  "error.login_failed": "فشل تسجيل الدخول.",
  "error.code_send_failed": "تعذر إرسال الرمز."
```

- [ ] **Step 3: Replace the literals in `frontend/app.js`**

**Line 348** (admin-required heading):
```javascript
    container.innerHTML = "<div class='card'>Admin access required to manage users.</div>";
```
→
```javascript
    container.innerHTML = `<div class='card'>${Khan.t("users.admin_required", "Admin access required to manage users.")}</div>`;
```

**Line 429** (groups heading):
```javascript
    heading.textContent = "Groups";
```
→
```javascript
    heading.textContent = Khan.t("users.groups_heading", "Groups");
```

**Line 473** (screen card site fallback):
```javascript
        <span>Site: ${escHtml(screen.site_name || "Unassigned")}</span>
```
→
```javascript
        <span>Site: ${escHtml(screen.site_name || Khan.t("screens.site_unassigned_label", "Unassigned"))}</span>
```

**Line 537** (preview meta — there's a multi-line template):
```javascript
  meta.textContent = `Previewing: ${screen.name} (${screen.site_name || "Unassigned"})` +
```
→
```javascript
  meta.textContent = `${Khan.t("screens.preview_meta_label", "Previewing")}: ${screen.name} (${screen.site_name || Khan.t("screens.preview_meta_unassigned", "Unassigned")})` +
```

(If a continuation line below uses the literal "expires", update it the same way using `Khan.t("screens.preview_meta_expires", "expires")`.)

**Line 601** (zone preview default name):
```javascript
    preview.innerHTML = `<div class="zone-title">New Zone</div>`;
```
→
```javascript
    preview.innerHTML = `<div class="zone-title">${Khan.t("screens.zone_default_name", "New Zone")}</div>`;
```

**Line 720** (owner select Unassigned option):
```javascript
  ownerSelect.innerHTML = `<option value="">Unassigned</option>`;
```
→
```javascript
  ownerSelect.innerHTML = `<option value="">${Khan.t("screens.access_unassigned_option", "Unassigned")}</option>`;
```

**Line 1039** (files uploaded toast):
```javascript
  toast(`${uploaded} file(s) uploaded.`, "success");
```
→
```javascript
  toast(Khan.t("toast.files_uploaded_n", "{n} file(s) uploaded.").replace("{n}", uploaded), "success");
```

**Line 1111** (login failed fallback):
```javascript
    toast(err.message || "Login failed.", "error");
```
→
```javascript
    toast(err.message || Khan.t("error.login_failed", "Login failed."), "error");
```

**Line 1194** (code send failed fallback):
```javascript
    toast(err.message || "Couldn't send code.", "error");
```
→
```javascript
    toast(err.message || Khan.t("error.code_send_failed", "Couldn't send code."), "error");
```

**Line 1266** (signup welcome toast):
```javascript
      toast(`Welcome to Khanshoof, ${signupState.business_name}! Your 5-day trial is active.`, "success", 6000);
```
→
```javascript
      toast(Khan.t("toast.signup_welcome", "Welcome to Khanshoof, {name}! Your 5-day trial is active.").replace("{name}", signupState.business_name), "success", 6000);
```

- [ ] **Step 4: Spot-check for any remaining English literals**

```bash
grep -nE "(textContent|innerHTML)\s*=\s*['\"\`][A-Z]" frontend/app.js | grep -v "Khan.t\|escHtml\|escape" | head -10
```

Manually inspect each remaining hit. If it's user-visible English without `Khan.t`, add a key for it in this commit. If it's a CSS class name or HTML structure (no human language), leave alone.

- [ ] **Step 5: Verify JS + parity**

```bash
node --check frontend/app.js && echo "JS OK"
python3 -c "import json; en=json.load(open('frontend/i18n/en.json')); ar=json.load(open('frontend/i18n/ar.json')); print('en:', len(en), 'ar:', len(ar), 'parity:', set(en)==set(ar))"
python3 scripts/check_i18n.py
```

Expected: `JS OK`, `en: 290 ar: 290 parity: True`, `i18n OK across frontend, landing, player`.

- [ ] **Step 6: Commit**

```bash
git add frontend/app.js frontend/i18n/en.json frontend/i18n/ar.json
git commit -m "$(cat <<'EOF'
feat(arabic-rtl): extract hardcoded English to i18n keys

Twelve user-visible English literals in frontend/app.js (admin-
required heading, groups heading, screen-site fallback, preview
meta label / unassigned / expires, zone-default name, access-owner
unassigned option, files-uploaded toast, signup welcome toast,
login + code-send error fallbacks) now go through Khan.t. New keys
added to en.json + ar.json with MSA Arabic.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Markup-only i18n + RTL fixes (resolution dropdown, lang-toggle aria, dir="auto", landing nav CSS)

**Files:**
- Modify: `frontend/index.html`
- Modify: `landing/index.html`
- Modify: `player/index.html`
- Modify: `landing/styles.css`
- Modify: `frontend/i18n/en.json` and `frontend/i18n/ar.json` (5 new keys).

- [ ] **Step 1: Add 5 new i18n keys to `frontend/i18n/en.json`**

```json
  "screens.resolution_fhd": "FHD (1920×1080)",
  "screens.resolution_2k": "2K (2560×1440)",
  "screens.resolution_4k_uhd": "4K UHD (3840×2160)",
  "screens.resolution_4k_dci": "4K DCI (4096×2160)",
  "nav.lang_toggle_aria": "Switch language"
```

- [ ] **Step 2: Add the same 5 keys to `frontend/i18n/ar.json`**

(Resolution names stay in Latin form per spec — they're technical shorthand.)

```json
  "screens.resolution_fhd": "FHD (1920×1080)",
  "screens.resolution_2k": "2K (2560×1440)",
  "screens.resolution_4k_uhd": "4K UHD (3840×2160)",
  "screens.resolution_4k_dci": "4K DCI (4096×2160)",
  "nav.lang_toggle_aria": "تبديل اللغة"
```

- [ ] **Step 3: Wire resolution dropdown options in `frontend/index.html`**

Find lines 244–247:

```html
            <option value="1920x1080">FHD (1920×1080)</option>
            <option value="2560x1440">2K (2560×1440)</option>
            <option value="3840x2160">4K UHD (3840×2160)</option>
            <option value="4096x2160">4K DCI (4096×2160)</option>
```

Replace with:

```html
            <option value="1920x1080" data-i18n="screens.resolution_fhd">FHD (1920×1080)</option>
            <option value="2560x1440" data-i18n="screens.resolution_2k">2K (2560×1440)</option>
            <option value="3840x2160" data-i18n="screens.resolution_4k_uhd">4K UHD (3840×2160)</option>
            <option value="4096x2160" data-i18n="screens.resolution_4k_dci">4K DCI (4096×2160)</option>
```

- [ ] **Step 4: Replace lang-toggle aria-label in `frontend/index.html`**

Find line 34:

```html
          <button id="lang-toggle" class="secondary-btn lang-toggle" aria-label="Switch language">
```

Replace with:

```html
          <button id="lang-toggle" class="secondary-btn lang-toggle" data-i18n-aria-label="nav.lang_toggle_aria" aria-label="Switch language">
```

(Keep the literal `aria-label="Switch language"` as a fallback for users with JS disabled.)

- [ ] **Step 5: Replace lang-toggle aria-label in `landing/index.html`**

Find line 32:

```html
          <button id="lang-toggle" class="lang-toggle" aria-label="Switch language">
```

Replace with:

```html
          <button id="lang-toggle" class="lang-toggle" data-i18n-aria-label="nav.lang_toggle_aria" aria-label="Switch language">
```

The landing app needs the same `nav.lang_toggle_aria` key. Append to `landing/i18n/en.json`:

```json
  "nav.lang_toggle_aria": "Switch language"
```

And to `landing/i18n/ar.json`:

```json
  "nav.lang_toggle_aria": "تبديل اللغة"
```

(If the landing app's i18n already has these keys, no-op; otherwise add.)

- [ ] **Step 6: Add `dir="auto"` to text inputs in `frontend/index.html`**

For the text inputs at lines 52, 61, 62, 219, 220, 230, 231, 240, 241, 250, 305, 328, 346, 359, 369 (all `<input type="text">` and the `<input type="email">` at line 62, plus the `<input type="url">` at line 329), add `dir="auto"` attribute.

Example: line 52
```html
          <input type="text"     id="login-username" placeholder="Email or username" data-i18n-placeholder="auth.email_or_username" required autocomplete="username" />
```
→
```html
          <input type="text"     id="login-username" placeholder="Email or username" data-i18n-placeholder="auth.email_or_username" required autocomplete="username" dir="auto" />
```

Apply this to ALL `<input type="text">`, `<input type="email">`, `<input type="url">`, `<input type="search">`, and `<textarea>` elements in the file.

**Skip:**
- `<input type="password">` (no display direction concern).
- `<input id="signup-otp" ...>` at line 73 — it's a 6-digit numeric code; add `dir="ltr"` instead so the digits always read LTR.

- [ ] **Step 7: Add `dir="auto"` to text inputs in `landing/index.html`**

```bash
grep -nE "<input type=\"(text|email|url|search)\"|<textarea" landing/index.html
```

For each match: add `dir="auto"` (or `dir="ltr"` if the input is a code/OTP field — landing probably has none).

- [ ] **Step 8: Add `dir="ltr"` to the player admin-code input**

Find `player/index.html:38`:

```html
            <input id="admin-code-input" type="text" maxlength="6" minlength="6"
```

Add `dir="ltr"`:

```html
            <input id="admin-code-input" type="text" maxlength="6" minlength="6" dir="ltr"
```

- [ ] **Step 9: Fix landing mobile nav-links physical CSS**

In `landing/styles.css`, find the `.nav-links` rule at ~line 271 with mobile dropdown styles. Inside that rule, find:

```css
  top: 100%;
  left: 0;
  right: 0;
```

Replace with:

```css
  inset-block-start: 100%;
  inset-inline: 0;
```

(Verify by `grep -n "left: 0\|right: 0\|top: 100%" landing/styles.css` — if there are other matches, leave them; only the `.nav-links` mobile-dropdown rule is in scope.)

- [ ] **Step 10: Verify HTML + parity**

```bash
python3 -c "import json; en=json.load(open('frontend/i18n/en.json')); ar=json.load(open('frontend/i18n/ar.json')); print('en:', len(en), 'ar:', len(ar), 'parity:', set(en)==set(ar))"
python3 -c "import json; en=json.load(open('landing/i18n/en.json')); ar=json.load(open('landing/i18n/ar.json')); print('landing en:', len(en), 'ar:', len(ar), 'parity:', set(en)==set(ar))"
python3 scripts/check_i18n.py
```

Expected: parity True for both, `i18n OK across frontend, landing, player`.

- [ ] **Step 11: Commit**

```bash
git add frontend/index.html landing/index.html player/index.html landing/styles.css frontend/i18n/en.json frontend/i18n/ar.json landing/i18n/en.json landing/i18n/ar.json
git commit -m "$(cat <<'EOF'
feat(arabic-rtl): markup i18n — resolution dropdown, lang-toggle aria, dir="auto"

Resolution dropdown options now translate via data-i18n (labels stay
in Latin form per spec). Lang-toggle button gains
data-i18n-aria-label across admin and landing. All user-text inputs
across admin, landing, and player gain dir="auto" for typed-content
direction detection (passwords, OTP, and pair-code inputs are
explicitly LTR or unscoped). Landing mobile nav dropdown now uses
inset-inline:0 instead of left:0;right:0 (logical CSS).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Player pairing-screen language toggle

**Files:**
- Modify: `player/i18n.js` (add `setCookie` + `setLocale`).
- Modify: `player/index.html` (add toggle button markup).
- Modify: `player/player.js` (wire click handler).
- Modify: `player/styles.css` (position the button).
- Modify: `player/i18n/en.json` and `player/i18n/ar.json` (add 2 keys if missing).

- [ ] **Step 1: Extend `player/i18n.js` with `setLocale`**

Open `player/i18n.js`. After the `applyTranslations` function (line 34) and before `detectInitialLocale` (line 36), insert:

```javascript
  function setCookie(locale) {
    const host = location.hostname;
    const isProd = host.endsWith("khanshoof.com");
    const domainAttr = isProd ? "; domain=.khanshoof.com" : "";
    document.cookie = `khanshoof_lang=${locale}${domainAttr}; path=/; max-age=31536000; samesite=lax`;
  }

  function setLocale(locale) {
    if (!ALLOWED.includes(locale)) locale = "en";
    setCookie(locale);
    return locale;
  }
```

Then update the `window.Khan` export at line 45:

From:
```javascript
  window.Khan = { loadLocale, t, applyTranslations, detectInitialLocale, currentLocale };
```

To:
```javascript
  window.Khan = { loadLocale, t, applyTranslations, setLocale, detectInitialLocale, currentLocale };
```

- [ ] **Step 2: Verify the i18n keys exist in player**

```bash
grep -E "lang.toggle_label|nav.lang_toggle_aria" player/i18n/en.json player/i18n/ar.json
```

If either key is missing in player, add to both files:

`player/i18n/en.json`:
```json
  "lang.toggle_label": "عربي",
  "nav.lang_toggle_aria": "Switch language"
```

`player/i18n/ar.json`:
```json
  "lang.toggle_label": "EN",
  "nav.lang_toggle_aria": "تبديل اللغة"
```

(Note the `lang.toggle_label` flip: the button shows the *target* language, so EN-mode shows "عربي" and AR-mode shows "EN".)

- [ ] **Step 3: Add the toggle button markup in `player/index.html`**

Find the pairing section (likely around the `admin-code-form` or a similar pairing wrapper). Add the toggle button as the first child of `<body>` so it's positioned absolutely regardless of layout:

```html
    <button id="player-lang-toggle" class="player-lang-toggle"
            type="button"
            data-i18n="lang.toggle_label"
            data-i18n-aria-label="nav.lang_toggle_aria"
            aria-label="Switch language">عربي</button>
```

(If the player already has a `<header>` or top-bar, place the button there instead. Otherwise body-level absolute is fine.)

- [ ] **Step 4: Wire the click handler in `player/player.js`**

Near the bottom of `player/player.js` (or wherever DOM listeners are set up; look for `DOMContentLoaded` or similar), add:

```javascript
  const langToggle = document.getElementById("player-lang-toggle");
  if (langToggle) {
    langToggle.addEventListener("click", async () => {
      const next = Khan.currentLocale() === "ar" ? "en" : "ar";
      Khan.setLocale(next);
      await Khan.loadLocale(next);
      Khan.applyTranslations();
    });
  }
```

If `player/player.js` already has a `DOMContentLoaded` listener, add the toggle wire-up inside it. If not, wrap in:

```javascript
document.addEventListener("DOMContentLoaded", () => {
  const langToggle = document.getElementById("player-lang-toggle");
  if (langToggle) {
    langToggle.addEventListener("click", async () => {
      const next = Khan.currentLocale() === "ar" ? "en" : "ar";
      Khan.setLocale(next);
      await Khan.loadLocale(next);
      Khan.applyTranslations();
    });
  }
});
```

- [ ] **Step 5: Position the button via CSS**

Append to `player/styles.css`:

```css
.player-lang-toggle {
  position: fixed;
  inset-block-start: 16px;
  inset-inline-end: 16px;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.4);
  border-radius: 999px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
}

.player-lang-toggle:hover {
  background: rgba(0, 0, 0, 0.7);
}
```

- [ ] **Step 6: Verify JS + parity**

```bash
node --check player/player.js && echo "player JS OK"
node --check player/i18n.js && echo "player i18n OK"
python3 scripts/check_i18n.py
```

Expected: both `OK`, `i18n OK across frontend, landing, player`.

- [ ] **Step 7: Commit**

```bash
git add player/i18n.js player/index.html player/player.js player/styles.css player/i18n/en.json player/i18n/ar.json
git commit -m "$(cat <<'EOF'
feat(arabic-rtl): player pairing-screen language toggle

The player's pre-pair screen was English-only. Adds a small fixed-
position language-toggle button (top-right LTR / top-left RTL via
inset-inline-end) showing the *other* language label (EN-mode shows
"عربي", AR-mode shows "EN"). Click flips locale via the new
Khan.setLocale (mirrors admin/landing) and re-applies translations.
Cookie persistence works across player + admin + landing because
they share the .khanshoof.com domain.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Regression sweep + manual smoke

**No new code.** Verification only.

- [ ] **Step 1: Backend regression**

```bash
docker exec -e DEV_MODE=1 -e RATE_LIMITS_ENABLED=0 signage_backend_1 pytest 2>&1 | tail -3
```

Expected: `150 passed`. (No backend changes in this plan; count is unchanged from main.)

- [ ] **Step 2: i18n parity (all three apps)**

```bash
python3 scripts/check_i18n.py
```

Expected: `i18n OK across frontend, landing, player`.

- [ ] **Step 3: JS sanity**

```bash
node --check frontend/app.js && echo "frontend OK"
node --check landing/landing.js && echo "landing OK"
node --check player/player.js && echo "player OK"
node --check player/i18n.js && echo "player i18n OK"
```

Expected: all `OK`.

- [ ] **Step 4: Rebuild + redeploy frontend, landing, player**

```bash
docker-compose build frontend landing player
docker-compose up -d --force-recreate frontend landing player
sleep 4
docker-compose ps frontend landing player | tail -4
```

Expected: all healthy.

- [ ] **Step 5: Manual smoke (record results in PR description)**

Open `https://app.khanshoof.com` in a browser:

1. **Confirm-modal AR sanity (Task 1):**
   - Switch admin to Arabic.
   - For each: site, playlist, media, user, group, screen, playlist item, wall, cell-unpair, canvas item — click delete/remove.
   - Each shows the new modal with title + body in Arabic, Cancel ("إلغاء") and Delete ("حذف") buttons.
   - Esc, Cancel button, backdrop click, and the ✕ button all dismiss without action.
   - Enter key confirms.
   - Verify the dialog is visually centered with the dim backdrop.

2. **Hardcoded-string extraction (Task 2):**
   - In Arabic mode: navigate Users tab as a non-admin → confirm "صلاحية المدير مطلوبة" appears (not "Admin access required").
   - Users tab: groups heading reads "المجموعات".
   - Screens tab: live preview area shows "جارٍ معاينة" / "غير مُعيَّنة" / "تنتهي" appropriately.
   - Screens tab → Zones: drag a new zone, default name reads "منطقة جديدة" (not "New Zone").
   - Media tab: upload some files, toast reads "تم رفع N ملفاً." with the right count.
   - Sign up a new account in Arabic — welcome toast in Arabic.

3. **Markup i18n (Task 3):**
   - Arabic mode → Screens tab → resolution dropdown → confirm options still show technical labels (FHD, 2K, 4K UHD, 4K DCI) — these stay Latin per spec.
   - Hover the language toggle button — `aria-label` (in browser dev tools) reads "تبديل اللغة".
   - Type into login username field with first character Latin → cursor stays LTR. Type Arabic first → cursor flips RTL.
   - Resize landing page to mobile (≤640px width). Switch to Arabic. Click hamburger nav → dropdown anchors flush, no shift.

4. **Player toggle (Task 4):**
   - Open `https://play.khanshoof.com` in a fresh browser window (clear cookies first).
   - Confirm a "عربي" button appears at top-right corner.
   - Click it → pairing screen flips to Arabic, button now reads "EN".
   - Reload the page → still Arabic (cookie persisted).
   - Click "EN" → flips back to English.

- [ ] **Step 6: No commit. Smoke results recorded in PR body.**

---

## Task 6: Finish development branch

- [ ] **Step 1: Verification before completion**

Required evidence:
- `pytest` final count: `150 passed`.
- `python3 scripts/check_i18n.py`: `i18n OK across frontend, landing, player`.
- All 4 manual smoke checkpoints (Task 5 Step 5) pass by eye.
- Zero remaining `confirm(` calls in `frontend/app.js`.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin feature/arabic-rtl-hardening
```

- [ ] **Step 3: Open PR via gh CLI**

```bash
~/.local/bin/gh pr create \
  --base main \
  --head feature/arabic-rtl-hardening \
  --title "feat(arabic-rtl): hardening — confirmDialog, string extraction, player toggle, dir=auto" \
  --body "[paste body — see template below]"
```

PR body template:

```markdown
## Summary
Phase 2.5b — Arabic / RTL hardening. Eliminates user-visible English in the admin when locale = Arabic, fixes one logical-CSS bug, and adds a language toggle to the player's pairing screen.

- **`confirmDialog` modal** replaces all 10 `confirm()` call sites in `frontend/app.js` (browser confirm() ignored our locale; new modal respects Khan.t and lives in the same `.modal` family as mode-change + media picker).
- **12 hardcoded English strings** extracted from `frontend/app.js` to i18n keys (admin-required heading, groups heading, preview meta, "New Zone", signup welcome toast, files-uploaded toast, login + code-send error fallbacks).
- **Resolution dropdown** in `frontend/index.html` now uses `data-i18n` (labels stay Latin per spec — technical shorthand).
- **Lang-toggle `aria-label`** translates via `data-i18n-aria-label` across admin and landing.
- **`dir="auto"`** on all user-text inputs in admin / landing / player. OTP / pair-code inputs explicitly LTR.
- **Landing mobile nav** dropdown uses `inset-inline: 0` (logical CSS) instead of `left:0; right:0`.
- **Player pairing screen** gains a language toggle button (top-right in LTR, top-left in RTL). `Khan.setLocale` added to `player/i18n.js` to mirror admin/landing.

**i18n key delta:** ~37 new keys (frontend ~32, landing 1, player 2 if missing). Parity-checked.

**Tests:** 150 passing (unchanged — no backend changes).

**Base:** main. No upstream dependencies.

## Test plan
- [x] `pytest` — 150 passed
- [x] `python3 scripts/check_i18n.py` — i18n OK
- [x] `node --check` on all four JS files
- [ ] Manual confirm-modal sanity (10 sites, AR mode) — pending
- [ ] Manual hardcoded-string sanity (5 spots, AR mode) — pending
- [ ] Manual markup sanity (resolution + aria + dir + landing nav) — pending
- [ ] Manual player toggle smoke — pending

## Phase 2.5c (next initiative — security)
Account lockout after N failed auth attempts, audit log for sensitive admin actions, password policy strengthening + breach-list check, 2FA / OTP for admin login, session-revocation UI.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

- [ ] **Step 4: Update memory**

Edit `/home/ahmed/.claude/projects/-home-ahmed-signage/memory/MEMORY.md` to add a pointer for Phase 2.5b. Create `project_arabic_rtl_hardening.md` with branch SHA + PR URL + remaining smoke status.

---

## Self-review

**1. Spec coverage:**

| Spec section | Implementing task |
|---|---|
| Section 1 — Custom confirm-modal + migrate all confirm() sites | Task 1 |
| Section 2 — Extract hardcoded English strings | Task 2 |
| Section 3 — Resolution dropdown translation | Task 3 |
| Section 4 — Lang-toggle aria-label | Task 3 |
| Section 5 — Player pairing-screen language toggle (incl. setLocale extension) | Task 4 |
| Section 6 — Landing mobile nav physical → logical CSS | Task 3 |
| Section 7 — Form inputs dir="auto" | Task 3 |
| Section 8 — Tests + smoke | Task 5 |
| Section 9 — Out of scope | (Documented; no task) |
| Section 11 — Done definition | Tasks 5 + 6 |

No gaps.

**2. Placeholder scan:**
- Step "Spot-check for any remaining English literals" in Task 2 Step 4 says "Manually inspect each remaining hit. If it's user-visible English without `Khan.t`, add a key for it in this commit." — this is a real instruction, not a placeholder. The grep is precise and the criterion ("user-visible English without Khan.t") is unambiguous.
- Task 3 Step 7 says "landing probably has none" for OTP/code inputs — this is a hint, not a placeholder. The grep at the start of the step shows the implementer exactly which files to check.
- Task 4 Step 2 says "If either key is missing in player, add to both files" — this is conditional, not a placeholder. The check is the grep above it.

No placeholders.

**3. Type consistency:**
- `confirmDialog({ title, message, confirmLabel, danger })` signature is consistent across all 10 migration call sites in Task 1.
- `Khan.t("key", "fallback").replace("{name}", value)` interpolation pattern is used identically wherever there's an interpolation.
- `setLocale(locale)` signature in Task 4 matches the admin/landing implementation we mirror.
- `lang.toggle_label` is consistently the *target* language (button shows what you'd switch *to*) per the existing admin/landing convention.

**4. Backwards-compat scan:**
- Old `playlists.add_item` key is already orphaned from Phase 2.5a — not touched here.
- Old i18n keys we're adding alongside (e.g., `walls.confirm_delete`) remain valid — we add new title keys without renaming existing message keys.

No issues found.

---

## Done

When this plan ships green:
- Zero `confirm(` calls in `frontend/app.js`.
- Zero hardcoded user-visible English strings in `frontend/app.js` (excluding intentional Latin technical names).
- Resolution dropdown gets data-i18n.
- Lang-toggle aria translates.
- Player pairing screen has a working language toggle.
- Landing mobile nav uses logical CSS.
- All text inputs respect typed-content direction.
- Phase 2.5c (security) starts next.
