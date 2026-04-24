# Khanshoof Rebrand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Swap every user-facing `Sawwii` / `sawwii.com` string in the app to `Khanshoof` / `khanshoof.com` and cut over Cloudflare Tunnel to the new domain. Hard cutover, text-only wordmark (no logo yet), English-only copy (Arabic i18n is a separate later project), Postgres DB name untouched.

**Architecture:** Mechanical text replacement across 11 source files plus 4 `.env` URL lines. The only non-text change is the `.env` domain swap that steers every browser-side `fetch` at the new API host. Cutover is sequenced so both domains are briefly live in parallel (new Cloudflare Tunnel routes added before containers rebuild), then the old routes are removed after smoke confirms.

**Tech Stack:** Existing stack — no new dependencies, no framework change, no database work. Vanilla HTML/JS/CSS, nginx, FastAPI, Postgres (renaming skipped), Docker Compose, Cloudflare Tunnel (user-managed).

**Branch:** Continue on `feature/player-qr-pairing`. HEAD is `1a265e0` (rebrand spec). Plan 2 + Plan 3 + rebrand commits merge to `main` together as one bundled "Khanshoof launch + end-to-end pairing" merge at the end.

**Not in scope (confirmed in spec):**
- Postgres DB / user / password — all still `sawwii` (infrastructure, invisible).
- `docker-compose.yml` Postgres refs — all three are DB config, untouched.
- `.env` `DATABASE_URL` — stays pointing at the real `sawwii` DB.
- `backend/db.py` fallback connection string — must match the real DB, so stays `sawwii`.
- Arabic i18n, RTL, language toggle — separate sub-project.
- Logo image — text-only `Khanshoof` ships this round.
- Historical plan docs referencing Sawwii — left as history.

---

## Prerequisites

```bash
cd /home/ahmed/signage
git status                                   # expect clean on feature/player-qr-pairing
docker-compose run --rm backend pytest       # expect 38 passed (baseline regression)
```

## File Structure (what changes)

```
.env                                         # 4 URL lines
frontend/config.js                           # 2 defaults
frontend/index.html                          # <title>, <h1>
frontend/app.js                              # 1 welcome string
player/config.js                             # 1 default
player/index.html                            # <title>, .pairing-brand, pair URL text
player/player.js                             # 1 APP_URL fallback
landing/index.html                           # 12 refs (title, hero, nav, pills, mailto, footer)
landing/app.js                               # 2 refs (comment + APP_URL default)
landing/styles.css                           # 1 comment
landing/docker-entrypoint.sh                 # APP_URL default
backend/main.py                              # 1 code comment
docs/superpowers/plans/2026-04-24-player-qr-pairing.md        # Sawwii refs in code blocks
docs/superpowers/plans/2026-04-24-admin-pair-page.md          # same
docs/superpowers/specs/2026-04-24-admin-pair-page-design.md   # same
/home/ahmed/.claude/projects/-home-ahmed-signage/memory/project_signage_saas_roadmap.md  # brand + domain facts
```

---

## Task 1: `.env` and config entrypoints

Swap every user-visible URL default. No containers rebuild yet — `.env` changes take effect on the next `docker-compose up`, which happens in Task 7.

**Files:** `.env`, `frontend/config.js`, `player/config.js`, `landing/docker-entrypoint.sh`

- [ ] **Step 1: Replace the four URL lines in `.env`**

Open `/home/ahmed/signage/.env`. Find:

```
APP_URL=https://app.sawwii.com
API_BASE_URL=https://api.sawwii.com
PLAYER_BASE_URL=https://play.sawwii.com
LANDING_URL=https://lets.sawwii.com
```

Replace with:

```
APP_URL=https://app.khanshoof.com
API_BASE_URL=https://api.khanshoof.com
PLAYER_BASE_URL=https://play.khanshoof.com
LANDING_URL=https://lets.khanshoof.com
```

Leave every other line in `.env` untouched. In particular: `DATABASE_URL=postgresql://sawwii:...@postgres:5432/sawwii` must stay exactly as it is.

- [ ] **Step 2: Replace `frontend/config.js`**

Full replacement (two lines, both values updated):

```javascript
window.API_BASE_URL = "https://api.khanshoof.com";
window.PLAYER_BASE_URL = "https://play.khanshoof.com";
```

- [ ] **Step 3: Replace `player/config.js`**

Full replacement (one line):

```javascript
window.API_BASE_URL = "https://api.khanshoof.com";
```

Note: in production this file is regenerated at container start by `player/docker-entrypoint.sh` from the `.env`. The checked-in file is the local-dev default.

- [ ] **Step 4: Update `landing/docker-entrypoint.sh`**

Find:

```sh
cat > /usr/share/nginx/html/config.js <<EOF
window.APP_URL = "${APP_URL:-https://app.sawwii.com}";
EOF
```

Replace with:

```sh
cat > /usr/share/nginx/html/config.js <<EOF
window.APP_URL = "${APP_URL:-https://app.khanshoof.com}";
EOF
```

- [ ] **Step 5: Verify**

```bash
grep -nE "sawwii" /home/ahmed/signage/.env /home/ahmed/signage/frontend/config.js /home/ahmed/signage/player/config.js /home/ahmed/signage/landing/docker-entrypoint.sh
```

Expected: **one** match — the `DATABASE_URL` line in `.env`. Any other match is a miss; fix before committing.

- [ ] **Step 6: Commit**

```bash
git -C /home/ahmed/signage add .env frontend/config.js player/config.js landing/docker-entrypoint.sh
git -C /home/ahmed/signage commit -m "chore: point service URLs at khanshoof.com"
```

---

## Task 2: Landing copy swap

Largest single block of text changes (12 refs in `index.html`, 2 in `app.js`, 1 in `styles.css`).

**Files:** `landing/index.html`, `landing/app.js`, `landing/styles.css`

- [ ] **Step 1: Replace `landing/index.html` strings**

Make these exact edits (each string is unique — `replace_all` is safe since no `Sawwii` appears in any context we want to preserve).

1. Line 7, `<title>`:
   - Before: `<title>Sawwii — Digital signage that just works</title>`
   - After:  `<title>Khanshoof — Digital signage that just works</title>`

2. Line 20, nav brand:
   - Before: `<span>Sawwii</span>`
   - After:  `<span>Khanshoof</span>`

3. Line 45, hero copy:
   - Before: `Sawwii turns any screen in your shop, clinic, or kiosk into a living menu board — priced fairly, bilingual by design, and ready for the Gulf market from day one.`
   - After:  `Khanshoof turns any screen in your shop, clinic, or kiosk into a living menu board — priced fairly, bilingual by design, and ready for the Gulf market from day one.`

4. Line 67, section pill:
   - Before: `<span class="pill" style="background: var(--rose);">Why Sawwii</span>`
   - After:  `<span class="pill" style="background: var(--rose);">Why Khanshoof</span>`

5. Line 95, feature paragraph:
   - Before: `<p>Paste a URL. Sawwii pulls your menu, your colours, your logo — and drafts a polished playlist you can edit in minutes.</p>`
   - After:  `<p>Paste a URL. Khanshoof pulls your menu, your colours, your logo — and drafts a polished playlist you can edit in minutes.</p>`

6. Line 215, mailto button:
   - Before: `<a class="btn btn-ghost" href="mailto:hello@sawwii.com">Contact us</a>`
   - After:  `<a class="btn btn-ghost" href="mailto:hello@khanshoof.com">Contact us</a>`

7. Line 229, AI section copy:
   - Before: `Paste your website or Instagram URL. Sawwii reads your menu, extracts your brand palette and logo, and drafts a polished playlist — ready to tweak and publish. The feature that killed our competitors' ten-hour designer bills.`
   - After:  `Paste your website or Instagram URL. Khanshoof reads your menu, extracts your brand palette and logo, and drafts a polished playlist — ready to tweak and publish. The feature that killed our competitors' ten-hour designer bills.`

8. Line 231, early-access CTA:
   - Before: `<a class="btn btn-primary btn-lg" href="mailto:hello@sawwii.com?subject=Sawwii%20AI%20early%20access">Get early access</a>`
   - After:  `<a class="btn btn-primary btn-lg" href="mailto:hello@khanshoof.com?subject=Khanshoof%20AI%20early%20access">Get early access</a>`

9. Line 286, closing CTA:
   - Before: `<a class="btn btn-ghost btn-lg" href="mailto:hello@sawwii.com">Talk to us</a>`
   - After:  `<a class="btn btn-ghost btn-lg" href="mailto:hello@khanshoof.com">Talk to us</a>`

10. Line 296, footer wordmark:
    - Before: `<span class="footer-wordmark">Sawwii</span>`
    - After:  `<span class="footer-wordmark">Khanshoof</span>`

11. Line 303, footer mailto:
    - Before: `<a href="mailto:hello@sawwii.com">Contact</a>`
    - After:  `<a href="mailto:hello@khanshoof.com">Contact</a>`

12. Line 305, copyright:
    - Before: `<div class="footer-copy">© 2026 Sawwii. Built in the Gulf.</div>`
    - After:  `<div class="footer-copy">© 2026 Khanshoof. Built in the Gulf.</div>`

- [ ] **Step 2: Replace `landing/app.js` strings**

1. Line 2, file-top comment:
   - Before: `   Sawwii landing — client behaviour`
   - After:  `   Khanshoof landing — client behaviour`

2. Line 7, APP_URL default:
   - Before: `  const APP_URL = (window.APP_URL || 'https://app.sawwii.com').replace(/\/+$/, '');`
   - After:  `  const APP_URL = (window.APP_URL || 'https://app.khanshoof.com').replace(/\/+$/, '');`

- [ ] **Step 3: Replace `landing/styles.css` comment**

Line 2, file-top comment:
   - Before: `   Sawwii landing — pastel retro-modern foundation`
   - After:  `   Khanshoof landing — pastel retro-modern foundation`

- [ ] **Step 4: Verify**

```bash
grep -nE "Sawwii|sawwii" /home/ahmed/signage/landing/*.html /home/ahmed/signage/landing/*.js /home/ahmed/signage/landing/*.css /home/ahmed/signage/landing/*.sh
```

Expected: **zero matches**.

- [ ] **Step 5: Commit**

```bash
git -C /home/ahmed/signage add landing/index.html landing/app.js landing/styles.css
git -C /home/ahmed/signage commit -m "feat(landing): rebrand Sawwii → Khanshoof"
```

---

## Task 3: Admin frontend copy swap

**Files:** `frontend/index.html`, `frontend/app.js`

- [ ] **Step 1: Replace `frontend/index.html` strings**

1. Line 6, `<title>`:
   - Before: `<title>Sawwii</title>`
   - After:  `<title>Khanshoof</title>`

2. Line 17, header brand:
   - Before: `<h1>Sawwii</h1>`
   - After:  `<h1>Khanshoof</h1>`

- [ ] **Step 2: Replace `frontend/app.js` string**

Line 1233, welcome toast:
   - Before: `` toast(`Welcome to Sawwii, ${signupState.business_name}! Your 14-day trial is active.`, "success", 6000); ``
   - After:  `` toast(`Welcome to Khanshoof, ${signupState.business_name}! Your 14-day trial is active.`, "success", 6000); ``

- [ ] **Step 3: Verify**

```bash
grep -nE "Sawwii|sawwii" /home/ahmed/signage/frontend/*.html /home/ahmed/signage/frontend/*.js
```

Expected: **zero matches**.

- [ ] **Step 4: Commit**

```bash
git -C /home/ahmed/signage add frontend/index.html frontend/app.js
git -C /home/ahmed/signage commit -m "feat(frontend): rebrand Sawwii → Khanshoof"
```

---

## Task 4: Player copy swap

**Files:** `player/index.html`, `player/player.js`

- [ ] **Step 1: Replace `player/index.html` strings**

1. Line 6, `<title>`:
   - Before: `<title>Sawwii Player</title>`
   - After:  `<title>Khanshoof Player</title>`

2. Line 18, `.pairing-brand`:
   - Before: `<h1 class="pairing-brand">Sawwii</h1>`
   - After:  `<h1 class="pairing-brand">Khanshoof</h1>`

3. Line 23, pair URL fallback text:
   - Before: `<li>On your phone, open <strong id="pairing-url">app.sawwii.com/pair</strong></li>`
   - After:  `<li>On your phone, open <strong id="pairing-url">app.khanshoof.com/pair</strong></li>`

- [ ] **Step 2: Replace `player/player.js` fallback**

Line 17, APP_URL fallback:
   - Before: `const APP_URL = (window.APP_URL || "").trim() || "https://app.sawwii.com";`
   - After:  `const APP_URL = (window.APP_URL || "").trim() || "https://app.khanshoof.com";`

- [ ] **Step 3: Verify**

```bash
grep -nE "Sawwii|sawwii" /home/ahmed/signage/player/*.html /home/ahmed/signage/player/*.js
```

Expected: **zero matches**. (The vendored `player/vendor/qrcode.js` is excluded by the glob; it never mentions either brand.)

- [ ] **Step 4: Commit**

```bash
git -C /home/ahmed/signage add player/index.html player/player.js
git -C /home/ahmed/signage commit -m "feat(player): rebrand Sawwii → Khanshoof"
```

---

## Task 5: Backend code comment + in-flight plan/spec docs

One-line backend comment. Plus the three in-flight docs that the active pairing plans refer to — these need to match the new brand so Plan 2 + 3 implementation tasks land Khanshoof strings in the code.

**Files:** `backend/main.py`, `docs/superpowers/plans/2026-04-24-player-qr-pairing.md`, `docs/superpowers/plans/2026-04-24-admin-pair-page.md`, `docs/superpowers/specs/2026-04-24-admin-pair-page-design.md`

- [ ] **Step 1: Update `backend/main.py` comment**

Line 199, dev email stub docstring:
   - Before: `plan once the sawwii.com DNS is pointed and an API key is issued.`
   - After:  `plan once the khanshoof.com DNS is pointed and an API key is issued.`

- [ ] **Step 2: Rewrite Sawwii/sawwii.com across the three in-flight docs**

For each file, do a full search-and-replace of `Sawwii` → `Khanshoof` and `sawwii.com` → `khanshoof.com`. Every match is in prose or in an embedded code block showing the brand string an engineer will later paste into source — both categories need the update.

```bash
cd /home/ahmed/signage
sed -i 's/Sawwii/Khanshoof/g; s/sawwii\.com/khanshoof.com/g' \
  docs/superpowers/plans/2026-04-24-player-qr-pairing.md \
  docs/superpowers/plans/2026-04-24-admin-pair-page.md \
  docs/superpowers/specs/2026-04-24-admin-pair-page-design.md
```

- [ ] **Step 3: Verify everywhere**

```bash
grep -rnE "Sawwii|sawwii\.com" /home/ahmed/signage/backend /home/ahmed/signage/frontend /home/ahmed/signage/player /home/ahmed/signage/landing /home/ahmed/signage/.env 2>/dev/null
grep -rnE "Sawwii|sawwii\.com" /home/ahmed/signage/docs/superpowers/plans/2026-04-24-player-qr-pairing.md /home/ahmed/signage/docs/superpowers/plans/2026-04-24-admin-pair-page.md /home/ahmed/signage/docs/superpowers/specs/2026-04-24-admin-pair-page-design.md
```

Expected: **zero matches**. (The regex is `Sawwii|sawwii\.com`, which does not match the `sawwii` literal in the `DATABASE_URL` line — that's intentional since the DB name is unchanged.)

- [ ] **Step 4: Commit**

```bash
git -C /home/ahmed/signage add backend/main.py \
  docs/superpowers/plans/2026-04-24-player-qr-pairing.md \
  docs/superpowers/plans/2026-04-24-admin-pair-page.md \
  docs/superpowers/specs/2026-04-24-admin-pair-page-design.md
git -C /home/ahmed/signage commit -m "docs: rebrand backend comment + in-flight pairing docs"
```

---

## Task 6: Update roadmap memory

**Files:** `/home/ahmed/.claude/projects/-home-ahmed-signage/memory/project_signage_saas_roadmap.md`

- [ ] **Step 1: Update brand + domain facts**

Read the current file. Find the `## Brand` section and update:

- Before: `- **Name:** Sawwii (سوّي) — Arabic imperative "make it"`
- After:  `- **Name:** Khanshoof (خنشوف) — Arabic "let's see" ("khalli nshoof"), phonetic play on "Artichoke"`

- Before: `- **Domain:** sawwii.com (user-owned, confirmed 2026-04-18)`
- After:  `- **Domain:** khanshoof.com (user-owned, rebrand hard-cutover 2026-04-24). Legacy sawwii.com retired.`

Find the `## Planned subdomains` section and replace the four bullets:

- `sawwii.com` → `khanshoof.com`
- `app.sawwii.com` → `app.khanshoof.com`
- `play.sawwii.com` → `play.khanshoof.com`
- `api.sawwii.com` → `api.khanshoof.com`

Find the DNS/Cloudflare live-map paragraph that reads `DNS is live via Cloudflare Tunnel as of 2026-04-22 — each subdomain maps to its container port (see "Hosted at" above). Root `sawwii.com` is owned but not yet serving; landing currently lives at `lets.sawwii.com`.` and replace it with:

`DNS is live via Cloudflare Tunnel as of 2026-04-24 (hard cutover from sawwii.com) — each subdomain maps to its container port (see "Hosted at" above). Root khanshoof.com is owned but not yet serving; landing currently lives at lets.khanshoof.com.`

Find the `Hosted at` block and replace the four `sawwii.com` hostnames with `khanshoof.com` equivalents.

In the `## Open items to confirm before resuming` section, update item 5:
- Before: `5. Decide whether to point root sawwii.com at landing (replacing lets.sawwii.com) before public launch.`
- After:  `5. Decide whether to point root khanshoof.com at landing (replacing lets.khanshoof.com) before public launch.`

Global sweep: any remaining `Sawwii` or `sawwii.com` in the file, replace with the khanshoof equivalent — EXCEPT leave historical merge-commit SHAs and phrases like "Merge landing page" intact. The safe command:

```bash
sed -i 's/Sawwii/Khanshoof/g; s/sawwii\.com/khanshoof.com/g' \
  /home/ahmed/.claude/projects/-home-ahmed-signage/memory/project_signage_saas_roadmap.md
```

Then read the file and spot-check: no reference to old domain survives, DB name is still documented as `sawwii` in infrastructure notes if present (no mention expected), and prose still reads coherently.

- [ ] **Step 2: Add a Resend-domain note**

In the `## Open items to confirm before resuming` section, update item 4:
- Before: `4. Resend API key + verified sender domain — needed to replace the dev OTP stub in send_signup_otp_email(). DEV_MODE=1 MUST be unset in docker-compose.yml before production launch.`
- After:  `4. Resend API key + verified sender domain (noreply@khanshoof.com) — needed to replace the dev OTP stub in send_signup_otp_email(). DEV_MODE=1 MUST be unset in docker-compose.yml before production launch.`

- [ ] **Step 3: Update the MEMORY.md index entry**

File: `/home/ahmed/.claude/projects/-home-ahmed-signage/memory/MEMORY.md`

Find the roadmap bullet:
- Before: `- [Signage SaaS roadmap](project_signage_saas_roadmap.md) — **ACTIVE PLAN.** Brand: Sawwii (sawwii.com). Multi-tenant SaaS, ladder pricing, Stripe→KNET, EN/AR bilingual. Phase 1 (multi-tenancy) NOT started.`
- After:  `- [Signage SaaS roadmap](project_signage_saas_roadmap.md) — **ACTIVE PLAN.** Brand: Khanshoof (khanshoof.com, rebranded 2026-04-24). Multi-tenant SaaS, ladder pricing, Stripe→KNET, EN/AR bilingual. Phase 1 (multi-tenancy) NOT started.`

- [ ] **Step 4: No commit (memory files live outside the repo)**

Memory files are in `~/.claude/projects/-home-ahmed-signage/memory/`, not the signage repo. They're not git-tracked here. The edits are persisted by writing the file; no commit step.

---

## Task 7: Cutover day — Cloudflare + rebuild + smoke + merge

Must be executed in sequence. Steps are labelled **[I do]** or **[user does]**. Do not proceed past a step until its check passes.

- [ ] **Step 1: [I do] Final pre-cutover state check**

```bash
cd /home/ahmed/signage
git status                                                              # expect clean on feature/player-qr-pairing
git log --oneline main..HEAD | head -20                                 # confirm rebrand commits stacked on pairing commits
grep -rnE "sawwii\.com|Sawwii" frontend/ player/ landing/ backend/ .env docker-compose.yml 2>/dev/null
```

Expected: last command returns **zero matches**. (The regex is `sawwii\.com|Sawwii`, deliberately avoiding the `sawwii` DB-user literal in `DATABASE_URL`.) Any other hit is a miss from Tasks 1-5; fix before proceeding.

- [ ] **Step 2: [user does] Add khanshoof.com routes in Cloudflare Tunnel**

In Cloudflare Zero Trust → Networks → Tunnels → (your tunnel) → Public Hostnames, **add** four new rows:

| Subdomain | Domain | Service |
|-----------|--------|---------|
| app       | khanshoof.com | `http://localhost:3000` |
| play      | khanshoof.com | `http://localhost:3001` |
| api       | khanshoof.com | `http://localhost:8000` |
| lets      | khanshoof.com | `http://localhost:3003` |

Leave the old `*.sawwii.com` rows in place for now. Ensure `khanshoof.com` DNS is managed by Cloudflare (same provider as sawwii.com, so adding the Public Hostname auto-creates the DNS record). Tell me when the four rows are saved.

- [ ] **Step 3: [I do] DNS sanity check**

```bash
for host in app api play lets; do
  echo -n "$host.khanshoof.com → "
  curl -sI --max-time 5 "https://$host.khanshoof.com/" | head -1
done
```

Expected: each line prints `HTTP/2 200` or `HTTP/2 404` (404 is OK — means Cloudflare reached the container, just no root route). `502`/`523`/DNS failure means propagation isn't complete; wait ~30 seconds and retry.

- [ ] **Step 4: [I do] Rebuild containers with new `.env`**

```bash
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build
docker-compose -f /home/ahmed/signage/docker-compose.yml ps
```

Expected: all containers `Up (healthy)`. No container restarts in the last minute.

- [ ] **Step 5: [I do] Smoke matrix on khanshoof.com**

Run each check; all must pass.

```bash
# 1-4: bare reachability
for host in app api play lets; do
  echo -n "$host.khanshoof.com → "
  curl -sI --max-time 5 "https://$host.khanshoof.com/" | head -1
done

# 5: player config.js emits khanshoof URLs
curl -s https://play.khanshoof.com/config.js

# 6: api health endpoint
curl -s https://api.khanshoof.com/health

# 9: no residual Sawwii references in served HTML
for host in app play lets; do
  echo "=== $host.khanshoof.com ==="
  curl -s "https://$host.khanshoof.com/" | grep -i 'sawwii' || echo "(clean)"
done

# 10: backend regression
docker-compose -f /home/ahmed/signage/docker-compose.yml run --rm backend pytest
```

Expected:
- 1-4: each returns `HTTP/2 200`.
- 5: both `window.API_BASE_URL = "https://api.khanshoof.com";` and `window.APP_URL = "https://app.khanshoof.com";`.
- 6: JSON with status.
- 9: each host prints `(clean)`.
- 10: `38 passed`.

Browser checks (run after the curl checks pass):

| # | What | Expected |
|---|------|----------|
| 6a | `https://app.khanshoof.com/` logged-in admin | Dashboard loads, header reads `Khanshoof`, network tab shows API calls to `api.khanshoof.com` |
| 7  | `https://play.khanshoof.com/` incognito | Pastel pairing view renders, QR URL in the dev-console-read `<img>` src encodes `https://app.khanshoof.com/pair?code=…` |
| 8  | `https://lets.khanshoof.com/` | Header, hero, footer all say `Khanshoof`; Contact/Early-access `mailto:` links go to `hello@khanshoof.com` |

If any check fails, STOP and diagnose before step 6.

- [ ] **Step 6: [user does] Remove old sawwii.com Cloudflare routes (hard cutover)**

In Cloudflare Zero Trust → Networks → Tunnels → (your tunnel) → Public Hostnames, **delete** the four `*.sawwii.com` rows. Optionally delete the `sawwii.com` CNAMEs at the registrar if you want the brand gone completely.

Confirm:

```bash
for host in app api play lets; do
  echo -n "$host.sawwii.com → "
  curl -sI --max-time 5 "https://$host.sawwii.com/" | head -1
done
```

Expected: each line shows a Cloudflare 530/1016/DNS failure. That's the hard cutover succeeding.

- [ ] **Step 7: [I do] Merge the bundled branch to `main`**

At this point `feature/player-qr-pairing` carries three distinct sub-projects: Plan 2 (player QR UI), Plan 3 (admin `/pair` page), and the rebrand. Before merging, Plan 3's implementation tasks must have run (Plan 3's plan doc at `docs/superpowers/plans/2026-04-24-admin-pair-page.md`). If Plan 3 hasn't been executed yet when you reach this step, stop here and run Plan 3 first; the merge waits.

When Plan 3 is done:

```bash
git -C /home/ahmed/signage checkout main
git -C /home/ahmed/signage merge --no-ff feature/player-qr-pairing -m "Merge Khanshoof launch: rebrand + end-to-end pairing (Plans 2 + 3 + rebrand)"
docker-compose -f /home/ahmed/signage/docker-compose.yml up -d --build
```

Expected: fast-forward-free merge; containers come up healthy.

- [ ] **Step 8: [I do] Post-merge state check + memory update**

```bash
git -C /home/ahmed/signage log --oneline -10
```

Record the merge commit SHA. Append to the roadmap memory under `## Current state`:

`**Khanshoof rebrand + end-to-end pairing** (`feature/player-qr-pairing` → merged 2026-04-24, commit <SHA>): renamed Sawwii → Khanshoof across landing/admin/player, hard-cutover from sawwii.com to khanshoof.com on Cloudflare Tunnel, shipped player QR pairing UI (Plan 2) + admin /pair?code=… page (Plan 3).`

Also in the memory file, in the `Still NOT done` list, promote the Arabic i18n + RTL work to "NEXT" (it was previously Phase 2).
