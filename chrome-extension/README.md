# MediaHub Push — Chrome Extension

One-click push video URLs to MediaHub for parsing and download.

## Install

1. Open `chrome://extensions/`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** → select this `chrome-extension/` folder

## Setup

1. Click the extension icon in Chrome toolbar
2. Enter your **API URL** (e.g., `https://mediahub.heygo.cn`)
3. Enter your **API Key** (generate one in MediaHub Settings → API Keys)
4. Click **Save**

## Usage

- **Right-click** on any page → **Push to MediaHub**
- **Keyboard shortcut**: `Alt+M`

The extension sends the current page URL to MediaHub. If the URL is a supported video platform (Douyin, Xiaohongshu, Bilibili, etc.), MediaHub will parse and download it.

## Permissions

- `storage` — save your API URL and key
- `contextMenus` — right-click menu
- `activeTab` — read current tab URL
- `scripting` — show toast notifications
