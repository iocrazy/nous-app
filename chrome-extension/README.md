# MediaHub Push — Chrome Extension

Push video URLs to MediaHub for parsing and download, and scan pages for images to import into the resource library.

## Install

1. Open `chrome://extensions/`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** → select this `chrome-extension/` folder

## Setup

1. Click the extension icon in Chrome toolbar
2. Enter your **API URL** (e.g., `https://mediahub.heygo.cn`)
3. Enter your **API Key** (generate one in MediaHub Settings → API Keys)
4. Click **Save**

### API Key scopes

| Feature | Required scopes |
|---------|----------------|
| Push (video URL) | `videos:fetch`, `tags:read` (+ `tags:write` for tag creation) |
| Scan Images | `teams:read`, `resources:read`, `resources:write` |

## Usage

### Push mode

- **Right-click** on any page → **Push to MediaHub**
- **Keyboard shortcut**: `Alt+M`
- Or open the popup, pick tags, and click **Push to MediaHub**

The extension sends the current page URL to MediaHub. If the URL is a supported video platform (Douyin, Xiaohongshu, Bilibili, etc.), MediaHub will parse and download it.

### Scan Images mode

1. Open the popup → **Scan Images** tab
2. Pick a **Scope** (personal or team) and an optional **Folder**
3. Click **Scan Page Images** — collects `<img>`, `srcset`, lazy-load, and CSS background images (up to 300, small icons filtered)
4. Select images in the grid (size filter + select all available)
5. Optionally enable **Auto Tag after import** (dispatches the AI classify workflow per image)
6. Click **Import** — images are fetched **in the browser with site cookies** (works for login-gated / referer-protected CDNs) and uploaded to the resource library

The import runs in the background service worker, so closing the popup does not interrupt it. Thumbnails and PNG prompt extraction happen automatically server-side after upload.

## Permissions

- `storage` — save your API URL and key
- `contextMenus` — right-click menu
- `activeTab` — read current tab URL
- `scripting` — show toast notifications, scan page images
- `declarativeNetRequest` — set the page Referer on image fetches (referer-gated CDNs)
