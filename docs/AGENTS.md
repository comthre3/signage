# Khanshoof for AI Agents

This guide is for AI agents (Claude, Cursor, Zed, custom agents, scripts) that want to drive a Khanshoof signage account programmatically — read what's playing, build playlists, schedule them, swap media.

If you're a human reading this to brief an agent: paste the link into your agent's context (`https://app.khanshoof.com/AGENTS.md`) and it'll know what to do.

---

## Pick your integration

| Use case | Path | What you set up |
|---|---|---|
| **AI assistant** (Claude Desktop, Cursor, Zed) connecting once for a user | **MCP** | User clicks "Sign in with Khanshoof" once in your client; OAuth handles the rest |
| **Script** or **CI job** running unattended | **API key** | Admin creates a `khan_live_*` key in the Khanshoof dashboard → key in environment |
| **3rd-party SaaS** integrating Khanshoof for many customers | **OAuth 2.1** | Register a dynamic client at `POST /oauth/register`; standard `authorization_code` + PKCE flow |

All three end up calling the same HTTP endpoints with a `Bearer` token. The MCP server is a thin wrapper that gives Claude/Cursor/etc. tool-shaped access; the OAuth + API-key paths give you raw HTTP.

---

## Authentication in 30 seconds

**MCP (recommended for AI assistants):**

Add this to your MCP client config (Claude Desktop's `~/Library/Application Support/Claude/claude_desktop_config.json` or equivalent):

```json
{
  "mcpServers": {
    "khanshoof": {
      "url": "https://api.khanshoof.com/mcp",
      "transport": "streamable_http"
    }
  }
}
```

Restart the client. It'll prompt "Sign in with Khanshoof" on first tool call — that's OAuth doing the work. After that, ~23 tools named `khanshoof_*` appear in the tool list.

**API key (recommended for scripts):**

```bash
export KHAN_KEY=khan_live_AbCd...   # from dashboard → API Keys
curl https://api.khanshoof.com/playlists \
  -H "Authorization: Bearer $KHAN_KEY"
```

**OAuth 2.1 (for SaaS integrations):**

Discovery: `GET https://api.khanshoof.com/.well-known/oauth-authorization-server` → returns full metadata. Standard `authorization_code` flow with PKCE-S256. Refresh tokens rotate per OAuth 2.1.

---

## Concept model

A **Khanshoof organization** owns everything below. One person can belong to one org.

- **Sites** — physical locations (e.g., "Salmiya Café", "Mall Lobby"). Just a labeling concept.
- **Screens** — TVs / kiosks / wall sections. Each has a `pair_code` that a physical screen enters once to start receiving content. You can't create screens via API — pairing requires a human at the dashboard.
- **Playlists** — ordered lists of media items (image / video / website URL), each with a duration. The thing that actually plays.
- **Schedules** — dayparting rules. "Show playlist A weekdays 9-5, playlist B otherwise."
- **Walls** — multi-screen displays where N screens act as one canvas.
- **Media** — image / video / website URL items reusable across playlists.

A typical Khanshoof setup: human pairs the screen once → agent builds and swaps playlists thereafter.

---

## Scopes

OAuth and API keys both use two scopes:

- `api:read` — list/get only. Safe to grant for read-only assistants.
- `api:rw` — full content management (create/update/delete playlists, schedules, media, screen assignments).

Write tools fail with `403 api.insufficient_scope` if the token only has `api:read`.

---

## Tool / endpoint cheat sheet

The 23 MCP tools map 1:1 to existing HTTP endpoints. Full HTTP reference: https://app.khanshoof.com/api-docs.html.

| Domain | Reads | Writes |
|---|---|---|
| **Org** | `khanshoof_get_organization` | — |
| **Sites** | `khanshoof_list_sites` | — |
| **Screens** | `khanshoof_list_screens`, `khanshoof_get_screen`, `khanshoof_get_screen_zones` | `khanshoof_assign_playlist_to_screen` |
| **Playlists** | `khanshoof_list_playlists`, `khanshoof_get_playlist` | `khanshoof_create_playlist`, `khanshoof_update_playlist`, `khanshoof_delete_playlist`, `khanshoof_add_playlist_item` |
| **Schedules** | `khanshoof_list_schedules`, `khanshoof_get_schedule` | `khanshoof_create_schedule`, `khanshoof_update_schedule`, `khanshoof_delete_schedule`, `khanshoof_set_schedule_rules` |
| **Walls** | `khanshoof_list_walls`, `khanshoof_get_wall` | `khanshoof_add_canvas_playlist_item` |
| **Media** | `khanshoof_list_media` | `khanshoof_add_media_url` |

If you're using an MCP client, calling `tools/list` returns the same set with full input schemas + descriptions. Always trust `tools/list` over this table — it's the live source.

---

## Recipe 1: "Here are 5 images — put them on the lobby screen"

**Goal:** human uploads photos to the agent → agent makes a playlist out of them → assigns it to a screen.

### What you need before starting

- Either an API key with `api:rw` scope OR an active MCP session
- The images **need public URLs**. Khanshoof's API does not accept multipart binary uploads — only URLs. Two ways:
  - The agent uploads to its own hosted artifact storage (e.g., Claude artifact URLs, S3, Cloudinary, image-hosting service) and gets a public URL per image
  - The human pre-uploads via the Khanshoof dashboard (drag-and-drop in the Media panel) and the agent uses `khanshoof_list_media` to find them

### Steps

```python
# Pseudocode — adapt to your agent's tool-call syntax
image_urls = [
    "https://cdn.your-host.com/photo1.jpg",
    "https://cdn.your-host.com/photo2.jpg",
    # ...
]

# 1. Register each image as a media item
media_ids = []
for url in image_urls:
    result = khanshoof_add_media_url(
        url=url,
        name=url.split("/")[-1],   # required field
    )
    media_ids.append(result["id"])

# 2. Create the playlist
playlist = khanshoof_create_playlist(
    name="Lobby Photos — Spring 2026",
)

# 3. Add each media item with a duration
for media_id in media_ids:
    khanshoof_add_playlist_item(
        playlist_id=playlist["id"],
        media_id=media_id,
        duration_seconds=8,   # 8 seconds per slide
    )

# 4. Find the lobby screen
screens = khanshoof_list_screens()
lobby = next(s for s in screens if "lobby" in s["name"].lower())

# 5. Assign the playlist to the lobby screen
khanshoof_assign_playlist_to_screen(
    screen_id=lobby["id"],
    playlist_id=playlist["id"],
)
```

The screen starts playing the new content on its next refresh tick (~10 seconds for online players).

### Common follow-ups

- **"Make the videos play for their natural length"** — set `duration_seconds` long enough; the player honors media-natural length for video.
- **"Reorder the items"** — currently no bulk reorder tool. Delete and re-add in the new order, or update via the dashboard.
- **"Replace one image"** — `khanshoof_delete_playlist(playlist_id)` + recreate, or use the dashboard.

---

## Recipe 2: "Here's a restaurant website — build me a menu screen"

**Goal:** agent fetches a restaurant menu page → creates a digital menu playlist with item names, prices, and images → assigns to the dining-room screen.

### What you need

- The website URL
- A way to fetch + parse HTML (Claude has this built-in; other agents need `requests`/`fetch`)
- An image-hosting strategy for any extracted images (see Recipe 1)
- API key with `api:rw` OR MCP

### Steps

```python
# 1. Agent fetches the menu page using its own web-fetch tool
html = fetch("https://restaurant.example.com/menu")

# 2. Parse — extract items as {name, description, price, image_url?}
items = parse_menu(html)
# items = [
#   {"name": "Cappuccino", "price": "1.5 KWD", "image_url": "https://..."},
#   {"name": "Espresso",    "price": "1.0 KWD", "image_url": None},
#   ...
# ]

# 3. For items with images, register the image as media.
#    For items without images, you can use the menu page URL itself as a
#    "website" media item that the player renders as a live web page,
#    OR generate a graphic via an image-gen API and re-host.
media_for_item = {}
for item in items:
    if item["image_url"]:
        m = khanshoof_add_media_url(
            url=item["image_url"],
            name=item["name"],
        )
        media_for_item[item["name"]] = m["id"]

# 4. Create the playlist
playlist = khanshoof_create_playlist(
    name=f"Menu — {restaurant_name}",
)

# 5. Add each item with a sensible dwell time
for item in items:
    if item["name"] in media_for_item:
        khanshoof_add_playlist_item(
            playlist_id=playlist["id"],
            media_id=media_for_item[item["name"]],
            duration_seconds=10,
        )

# 6. Find the dining-room screen
screens = khanshoof_list_screens()
dining = next(s for s in screens if "dining" in s["name"].lower())

# 7. Assign
khanshoof_assign_playlist_to_screen(
    screen_id=dining["id"],
    playlist_id=playlist["id"],
)
```

### Things to tell the human ahead of time

- "Some items don't have images on your menu page — I can show them as text overlays via the dashboard's HTML editor, or generate visuals."
- "Prices/descriptions don't have a dedicated field — they show via the image itself. If your menu changes prices, we'll need to either re-fetch and re-build, or use website-mode media that always shows the live menu page."

---

## Recipe 3: "Show different content at different times"

**Goal:** weekday-morning breakfast menu, weekday-evening dinner menu, weekend brunch menu.

```python
breakfast = khanshoof_create_playlist(name="Breakfast menu")
dinner    = khanshoof_create_playlist(name="Dinner menu")
brunch    = khanshoof_create_playlist(name="Weekend brunch")

# (Populate each with khanshoof_add_playlist_item as in Recipe 1)

schedule = khanshoof_create_schedule(name="Café dayparting")

khanshoof_set_schedule_rules(
    schedule_id=schedule["id"],
    rules=[
        # Mon-Fri 7am-11am → breakfast
        *[{"day_of_week": d, "start_time": "07:00", "end_time": "11:00",
           "playlist_id": breakfast["id"]} for d in range(1, 6)],
        # Mon-Fri 17:00-22:00 → dinner
        *[{"day_of_week": d, "start_time": "17:00", "end_time": "22:00",
           "playlist_id": dinner["id"]} for d in range(1, 6)],
        # Sat-Sun 10:00-15:00 → brunch
        *[{"day_of_week": d, "start_time": "10:00", "end_time": "15:00",
           "playlist_id": brunch["id"]} for d in range(6, 8) if d < 8],
        # Note: day_of_week uses 0=Sun, 1=Mon, ..., 6=Sat in the player
    ],
)
```

Then assign the schedule to a screen via the dashboard (no API endpoint for schedule-to-screen attachment yet — v1 limitation).

---

## What you can't do (v1 limitations)

- **Binary upload over the API** — media-from-URL only. Images must be hosted somewhere with a public URL first.
- **Create or delete screens via API** — pairing requires a human at the dashboard, by design.
- **Bulk reorder playlist items** — no `set_playlist_items` (just `add_playlist_item`). Need to delete + recreate to reorder.
- **User / API-key / site CRUD via API** — admin-dashboard only.
- **Schedule-to-screen attachment via API** — dashboard only.
- **Multi-org users** — one identity = one org (one-to-one).

If any of these blockers stop you from accomplishing what the human asked, **tell the human exactly what you can't do**, don't fake it.

---

## Error handling

Every tool can fail with structured JSON. Map the responses to user-facing messages:

| HTTP / MCP code | Meaning | What to tell the human |
|---|---|---|
| `401` / MCP `-32600` | Token expired / revoked | "Your session expired — try the action again, I'll re-authenticate." |
| `403` with `code: api.insufficient_scope` | Read-only scope tried to write | "I have read-only access. Grant write access at app.khanshoof.com/api-keys." |
| `404` | Resource doesn't exist | "Couldn't find that — was it deleted, or do I have the wrong ID?" |
| `429` | Rate limited | "Too many requests — try again in {Retry-After} seconds." |
| `422` / MCP `-32602` | Validation error | Inspect `detail.errors` for specifics |
| `503` with `code: provider_not_configured` | Apple/Google not configured (signup flow only) | Not your problem; this is a Khanshoof operator concern |

OAuth-token errors carry `data.code` and `data.http_status` for programmatic handling.

---

## Discovering current tools

`tools/list` over MCP returns the live tool set. This guide may drift as new tools land — trust the live list.

For HTTP/API-key callers: `GET /api-docs.html` lists every endpoint, and the OpenAPI schema at `GET /openapi.json` is machine-readable.

---

## Etiquette

- Don't loop tool calls without a clear goal — Khanshoof has per-API-key rate limits (30/min on Starter, scaling up by plan).
- Don't create dozens of empty playlists trying to find one that fits — build, populate, assign in one logical sequence.
- Don't delete other agents' work without checking with the human first. Especially: don't `khanshoof_delete_playlist` for a playlist named "Live" or one currently assigned to a screen unless you know it's intentional.
- When in doubt: tell the human what you're about to do and wait for confirmation.

---

## Questions or feedback

Bugs, missing tools, broken examples: open an issue at the Khanshoof support email (see your invoice or the dashboard footer).

Phase next-up: bulk-replace playlist items, schedule-to-screen API attachment, binary media upload tool, Microsoft + Apple sign-in. Existing tools won't break.
