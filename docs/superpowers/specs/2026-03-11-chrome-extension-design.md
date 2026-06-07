# MediaHub Push — Chrome Extension Design

## Overview

A minimal Chrome extension (Manifest V3) that lets users push the current page's URL to MediaHub for video parsing/download with one click. Configuration via popup, feedback via toast notification.

## Architecture

```
popup.html/css/js    ← Config page: API URL + API Key
background.js        ← Service Worker: handles push requests
content.js           ← Injected into pages: shows toast notifications
manifest.json        ← Manifest V3 configuration
```

## User Flow

1. **First use**: Click extension icon → popup opens → enter API URL + API Key → Save
2. **Daily use**: Navigate to a video page → right-click "Push to MediaHub" or press `Alt+M` → toast shows result

## Components

### Popup (Configuration)

- Two input fields: **API URL** (e.g., `https://mediahub.heygo.cn`), **API Key**
- Save button → stores to `chrome.storage.sync`
- Connection status indicator (configured / not configured)
- Minimal styling, similar to OneNav extension popup

### Background Service Worker

- Registers context menu item "Push to MediaHub" on install
- Registers keyboard shortcut `Alt+M`
- On trigger: reads current tab URL + saved config from `chrome.storage.sync`
- Calls backend API:
  ```
  POST {apiUrl}/api/v1/videos/fetch
  Headers: X-API-Key: {apiKey}, Content-Type: application/json
  Body: { "url": "{currentTabUrl}", "video_bool": true, "cover_bool": true }
  ```
- Sends result to content script for toast display

### Content Script (Toast)

- Injected on demand (not on every page)
- Shows toast notification in top-right corner:
  - Pushing: "Pushing to MediaHub..."
  - Success: "Pushed! Parsing started." (auto-dismiss 3s)
  - Error: "Push failed: {error}" (auto-dismiss 5s)
- Toast styled with fixed positioning, z-index high enough to overlay page content

## File Manifest

| File | ~Lines | Purpose |
|------|--------|---------|
| `manifest.json` | 40 | Extension config, permissions, commands |
| `popup.html` | 30 | Config page markup |
| `popup.css` | 60 | Config page styles |
| `popup.js` | 40 | Config save/load logic |
| `background.js` | 60 | Push logic, context menu, commands |
| `content.js` | 50 | Toast notification injection |
| `icons/icon-16.png` | — | Toolbar icon |
| `icons/icon-48.png` | — | Extension management icon |
| `icons/icon-128.png` | — | Chrome Web Store icon |

Total: ~280 lines of code.

## Permissions

```json
{
  "permissions": ["storage", "contextMenus", "activeTab"],
  "host_permissions": ["<all_urls>"]
}
```

- `storage` — persist API URL + API Key
- `contextMenus` — right-click menu item
- `activeTab` — read current tab URL on trigger
- `host_permissions` — send fetch request to user-configured API URL

## API Integration

Uses existing MediaHub endpoint:
- **Endpoint**: `POST /api/v1/videos/fetch`
- **Auth**: `X-API-Key` header (requires `videos:fetch` scope)
- **Request**: `{ "url": "...", "video_bool": true, "cover_bool": true }`
- **Response**: `{ "success": true, "platform_id": "...", "message": "..." }`

No new backend changes needed — the existing API and API Key auth system fully support this.

## Design Decisions

1. **Generic URL push** (not platform-specific): Backend determines platform support. Extension needs no updates when new platforms are added.
2. **Manifest V3**: Required for new Chrome extensions, uses service workers instead of background pages.
3. **chrome.storage.sync**: Config syncs across user's Chrome instances.
4. **Content script injected on demand**: Only injected when user triggers a push, not on every page load — minimal performance impact.
5. **No state tracking**: Extension doesn't poll for parse status. User checks MediaHub dashboard for results. Keeps extension simple.
