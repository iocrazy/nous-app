# Nous Push — Chrome Extension

Push video URLs to Nous for parsing and download, and scan pages for images to import into the resource library.

## Install

Build a release copy first — Chrome remembers whichever folder you pick, and
pointing it at this source folder means branch switches, `git clean`, and
uncommitted work all land straight in the extension you use every day.

From the repo root:

```bash
bash scripts/package-extension.sh   # → release/chrome-extension/
```

1. Open `chrome://extensions/`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** → select `release/chrome-extension/`

Note: `release/` is gitignored, so `git clean -ffdx` deletes the built copy
along with everything else it's meant to protect you from — that tradeoff is
intentional (see
[`docs/superpowers/specs/2026-08-13-chrome-extension-packaging-design.md`](../docs/superpowers/specs/2026-08-13-chrome-extension-packaging-design.md)).
If that happens, just re-run the script.

After pulling new code, re-run the script, then hit the reload icon on the
extension card. The popup header shows `v1.4.0 (<commit>)` so you can tell at a
glance which build is loaded — if that commit does not match `git log -1`, the
release copy is stale and needs a re-run. A `-dirty` suffix means it was built
with uncommitted changes.

Loading this `chrome-extension/` folder directly still works for debugging; the
popup then shows the bare version with no commit.

## Setup

1. Click the extension icon in Chrome toolbar
2. Enter your **API URL** (e.g., `https://cn.nous.ink:88`)
3. Enter your **API Key** (generate one in Nous Settings → API Keys)
4. Enter your **Web URL** (e.g., `https://app.nous.ink`) — used for the "Open in nous" / "Generate Similar" deep links from the Prompt Analysis panel
5. Click **Save**

### API Key scopes

| Feature | Required scopes |
|---------|----------------|
| Push (video URL) | `videos:fetch`, `tags:read` (+ `tags:write` for tag creation) |
| Scan Images | `teams:read`, `resources:read`, `resources:write` |
| Analyze Prompt | `teams:read`, `resources:read`, `resources:write`, `tags:read`, `tasks:read` |

## Usage

### Push mode

- **Right-click** on any page → **Push to Nous**
- **Keyboard shortcut**: `Alt+M`
- Or open the popup, pick tags, and click **Push to Nous**

In the popup, above the Push button:

- **Rating** — five stars in one row; tap a star to rate, tap the same star again to clear
- **Transcribe / Summary / Analyze** — icon toggles sent as the `transcribe` / `summarize` / `analyze` request fields. Summary or Analyze turns Transcribe on too; turning Transcribe off turns both off
- Tags in the `Pipeline` group (Transcript / Summary / Analyze) are hidden from the tag list, search, and the create-tag group dropdown — the toggles replace them
- Tags and options are kept after a successful push (the popup never resets its selection), so pushing another URL reuses them

The extension sends the current page URL to Nous. If the URL is a supported video platform (Douyin, Xiaohongshu, Bilibili, etc.), Nous will parse and download it.

### Scan Images mode

1. Open the popup → **Scan Images** tab
2. Pick a **Scope** (personal or team) and an optional **Folder**
3. Click **Scan Page Images** — collects `<img>`, `srcset`, lazy-load, and CSS background images (up to 300, small icons filtered)
4. Select images in the grid (size filter + select all available)
5. Optionally enable **Auto Tag after import** (dispatches the AI classify workflow per image)
6. Click **Import** — images are fetched **in the browser with site cookies** (works for login-gated / referer-protected CDNs) and uploaded to the resource library

The import runs in the background service worker, so closing the popup does not interrupt it. Thumbnails and PNG prompt extraction happen automatically server-side after upload.

### Analyze Prompt mode

1. **Right-click any image** on a page → **Analyze Prompt (nous)**
2. A dark floating panel opens in the top-right corner and walks through:
   - Uploading the image to your personal resource library
   - Dispatching the reverse-prompt (gen-prompt) AI task
   - Polling progress every 2s (gives up watching — not cancelling — after 90s; the task keeps running server-side, check Task Center if it's still going)
3. On completion, the panel shows a result card:
   - **中文 / EN / JSON** tabs (JSON is pretty-printed, read-only)
   - Category / aspect-ratio chips (when the model returned structured data)
   - "N tags saved to library" (only shown when the workflow's auto-tags exist)
   - **Copy Prompt** (copies whichever tab is active), **⚡ Generate Similar**, **Open in nous** — the last two open the resource's detail page in a new tab (`Generate Similar` adds `?generateSimilar=1` to auto-open the same-flow modal there)
4. Right-clicking another image on the same page reuses the same panel instance instead of stacking a new one
5. Upload / dispatch / task-failed / timeout each show a distinct message with a **Retry** button

## Permissions

- `storage` — save your API URL, API key, and web URL
- `contextMenus` — right-click menu
- `activeTab` — read current tab URL
- `scripting` — show toast notifications, scan page images, inject the prompt-analyze panel
- `declarativeNetRequest` — set the page Referer on image fetches (referer-gated CDNs)

## Manual test checklist

The extension has no build step. The pure Push-tab logic in `intents.js` (intent dependencies, request fields, Pipeline filter, error-envelope messages) is unit-tested with `node --test scripts/extension-intents.test.cjs`; for everything else run `node --check <file>.js` on every touched file, then walk through this checklist against a real backend:

- [ ] `chrome://extensions` → reload the unpacked extension, confirm the popup header version matches `chrome-extension/manifest.json`'s `version` field — the `release/chrome-extension/` build also appends a `(<commit>)` suffix, while loading the `chrome-extension/` source folder directly does not
- [ ] Settings: enter API URL / API Key / Web URL, Save, reopen popup → all three persist
- [ ] Push tab: no Pipeline tags in the list, Frequently Used, search results, or the "+" group dropdown; searching "Summary" says it is an AI option instead of offering Create
- [ ] Rating: stars stay on one row; tap 3 → stars 1–3 lit; tap 3 again → none lit
- [ ] Toggles: Summary on → Transcribe lights too; Transcribe off → Summary and Analyze go dark; Analyze off leaves Transcribe on
- [ ] Push with rating + toggles → the new resource has the rating and the Transcript / Summary / Analyze pipeline tags; a failing push shows the server's error text (never "undefined")
- [ ] Right-click an image on any page → **Analyze Prompt (nous)** appears in the context menu and only for images (not on plain page right-click)
- [ ] Click it → panel opens top-right, dark card, progress bar animates through "Uploading image…" → "Starting analysis…" → "Analyzing image…"
- [ ] On completion: 中文/EN/JSON tabs all populated and switchable; JSON tab is pretty-printed and read-only; category/aspect-ratio chips show when present
- [ ] If the resource has AI tags: "N tags saved to library" line appears; if not, the line is omitted (not "0 tags")
- [ ] **Copy Prompt** copies the currently active tab's text (verify by pasting) — switching tabs then copying copies the new tab's content
- [ ] **⚡ Generate Similar** opens `{webUrl}/resources/file/{id}?generateSimilar=1` in a new tab
- [ ] **Open in nous** opens `{webUrl}/resources/file/{id}` (no query) in a new tab
- [ ] Right-click a second image on the same page → same panel instance is reused (no duplicate panel stacked)
- [ ] Close panel (×), verify no leftover polling network requests (check the service worker's Network/console)
- [ ] Failure paths (simulate by pointing API URL at a broken/unreachable host, or a key missing scopes):
  - [ ] Upload failure → "Upload failed: …" + Retry
  - [ ] Dispatch failure (e.g. API key missing `resources:write`) → "Couldn't start analysis: …" + Retry
  - [ ] Task failure (image resource that the backend rejects, e.g. non-image) → "Analysis failed…" + Retry
  - [ ] Timeout (simulate with a very slow backend, or temporarily lower `POLL_TIMEOUT_MS`) → "Still analyzing…" + Retry
  - [ ] Retry from any failure state resumes correctly (re-uploads only if no resource was created yet; otherwise re-dispatches on the existing resource)
