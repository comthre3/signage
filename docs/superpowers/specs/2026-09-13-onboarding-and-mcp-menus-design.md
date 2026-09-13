# Onboarding to First Screen, and Brand-Aware MCP Menus

**Status:** approved 2026-09-13
**Scope:** two independently buildable specs that meet at the menu model.

## Why

A new customer signs up and lands on nine equally-weighted, completely blank
tabs. `renderScreens()` and `renderPlaylists()` clear their container and
iterate an empty array, so the panes render literally nothing — no empty state,
no call to action. Signup creates an organization, a user and a session, and
nothing else.

Separately, an agent connected over MCP has 23 tools and none of them touch
menus, so the flagship feature is unreachable from the integration the product
sells as its automation story.

## Established constraints

- **Customers onboard at a desk; the TV comes later**, possibly days later,
  possibly configured by somebody else. The first success moment therefore
  cannot be "the screen lights up" — it has to be visible on a laptop.
- **The product is bilingual (EN/AR) for the GCC.** Templates bundle
  `IBM Plex Sans Arabic`. Typography is out of scope for brand matching:
  most brand fonts carry no Arabic glyphs, and substituting one silently
  breaks half of every menu.
- **The Playwright renderer must not be a dependency of signup.** It is needed
  to put a menu on a screen, not to show one to its author.

---

# Spec 1 — Onboarding to first screen live

## 1.1 Instant, renderer-free menu preview

`menu_render.build_html(tree, template_id, kind, language, aspect, ...)` already
produces a complete menu as HTML and is called only internally, immediately
before handing HTML to Playwright. Today a customer who builds a menu cannot see
it at all until they trigger an async render; the dashboard says "Not rendered
yet".

Add `GET /menus/{menu_id}/preview` returning that HTML directly:

- Query params `kind` (default `board`), `language` (default `en`), `aspect`
  (default `16:9`), validated against the template's declared `kinds`/`aspects`.
- Auth: same org scoping as `GET /menus/{menu_id}`.
- Returns `text/html`. No renderer call, no job, no 202.
- The dashboard embeds it in the menu editor, scaled to fit, refreshing on edit.

This is the desk-side "aha" and the single highest-value item in this spec.

**Framing note:** the dashboard's CSP has `frame-src https://play.khanshoof.com`
only. Embedding same-origin preview HTML is permitted by `default-src 'self'`,
but serving it from the API origin is not. Serve it from the app origin, or add
the API origin to `frame-src`. Verify in a browser — this exact class of bug
silently blanked the Screens preview for months.

## 1.2 Seed a working sample at signup

`POST /auth/signup/complete` additionally creates:

- a default site named after the business, and
- one sample menu built from a template, **definition only, never rendered**.

The sample is marked by an explicit `is_sample BOOLEAN NOT NULL DEFAULT false`
column on `menus` — not inferred from its name, so "clear the sample" is exact.
A customer who edits the sample into their real menu clears the flag on first
update rather than accumulating a hidden marker.

Sample content must never reach a real screen unnoticed: `POST
/menus/{id}/playlist` warns when publishing a row that still carries
`is_sample`.

Seeding must not fail signup. Wrap it so an error is logged and the account is
still created — an empty dashboard is a worse first run than no sample, but a
failed signup is worse than both.

## 1.3 Replace blank panes with a persisted checklist

Four steps, server-derived rather than stored as client state so it survives
logout and is correct across devices:

1. Add media or edit the sample menu
2. Preview it
3. Create a playlist (or publish the menu to one)
4. Pair a screen

`GET /onboarding/status` computes each from existing rows (media count, menu
count, playlist count, screen count). No new state to drift.

Screens, playlists, media and menus each get a real empty state — what the
section is for, and the one action that advances it.

## 1.4 Pairing that survives a handoff

Pairing may be done days later by another person. The screens section gains a
shareable instruction view: the pairing URL, what to open on the TV, and what
the code looks like — no dashboard login required to follow it.

Out of scope: changing the pairing protocol itself. It works; the 6-character
fix landed this session.

---

# Spec 2 — MCP menu tools, brand-aware

## 2.1 The missing tools

Six tools, matching the naming and dual-auth of the existing 23:

| Tool | Purpose |
|---|---|
| `khanshoof_list_menu_templates` | The three templates **with mood descriptors** |
| `khanshoof_create_menu` | Name, template, brand, categories and items |
| `khanshoof_get_menu` | Read one back |
| `khanshoof_update_menu` | Edit structure or brand |
| `khanshoof_render_menu` | Trigger the render job (202) |
| `khanshoof_publish_menu_to_playlist` | Renders → playlist |

## 2.2 Steering, not scraping

**The agent fetches; we never do.** No scraper, no SSRF surface, and we are not
the party retrieving content from Talabat or any ordering platform.

Steering lives in tool descriptions, which is what agents actually read.
`khanshoof_create_menu`'s description instructs: if the establishment has a
website or ordering-platform page, visit it first and carry its identity across
— palette into `primary`/`accent`/`background`, logo via `add_media_url` then
`logo_media_id`, and choose the template whose mood matches.

`khanshoof_list_menu_templates` returns mood descriptors ("warm, casual café",
"upscale steakhouse", "premium minimal") so that choice is informed rather than
arbitrary.

When `create_menu` arrives with default colors and no logo, the response carries
a non-blocking `hint` suggesting brand extraction. An agent without web access
still succeeds; an agent that simply forgot gets a second chance.

## 2.3 Make the templates actually themeable

`Brand` already carries `primary`, `accent`, `background`, `logo_media_id`,
taglines and currency, and the templates already use `var(--primary)` and
`var(--accent)` — but only partially: `cream-cafe` has 5 hardcoded colors
against 2 variables, `dark-classic` 2 against 3, `luxe` 3 against 1.

Setting brand colors today would therefore leave most of each template ignoring
them. Every hardcoded color becomes a token derived from the brand triple, with
surface and text tokens derived rather than authored so a two-color brand still
themes a whole board.

## 2.4 Contrast guard

A palette that is elegant on a laptop can be unreadable on a board four metres
away. After resolving tokens, compute WCAG contrast between text and its
surface. Below threshold, adjust the **text** token's lightness — never the
brand's own colors — until it passes, and return a warning naming what was
changed and why.

The brand still reads as theirs; the menu stays legible. Auto-correct rather
than refuse: an agent that gets a hard error mid-run tends to retry with
something worse, and a slightly darkened caption is a better outcome than a
failed menu.

Threshold: 4.5:1 for body text, 3:1 for large display text.

## Testing

- `build_html` preview: renders for every template × kind × aspect combination
  the template declares, and rejects combinations it does not.
- Sample seeding: a signup failure in seeding still yields a usable account.
- `is_sample` clears on first real edit; publishing a sample warns.
- Onboarding status derives correctly from an empty org and a populated one.
- Contrast guard: a deliberately low-contrast brand triple produces a passing
  result plus a warning, and the brand's own colors are unmodified.
- MCP tools: org scoping holds (an API key cannot read another org's menus),
  matching the existing cross-org tests.

## Explicitly out of scope

- Typography matching (Arabic glyph coverage).
- Backend scraping of customer or ordering-platform sites.
- Bespoke per-brand templates.
- Changing the pairing protocol.
