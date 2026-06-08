# MediaHub Push Chrome Extension — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a minimal Chrome extension (Manifest V3) that one-click pushes the current page URL to MediaHub for video parsing.

**Architecture:** Popup for config (API URL + API Key stored in `chrome.storage.sync`), background service worker for push logic (context menu + keyboard shortcut), content script injected on demand for toast notifications.

**Tech Stack:** Chrome Extension Manifest V3, vanilla JS, Chrome APIs (`storage`, `contextMenus`, `activeTab`, `scripting`)

**Spec:** `docs/superpowers/specs/2026-03-11-chrome-extension-design.md`

---

## File Structure

```
chrome-extension/
├── manifest.json       # Extension config, permissions, commands
├── popup.html          # Config page markup
├── popup.css           # Config page styles
├── popup.js            # Config save/load logic
├── background.js       # Service worker: push logic, context menu, commands
├── content.js          # Toast notification (injected on demand)
└── icons/
    ├── icon-16.png     # Toolbar icon
    ├── icon-48.png     # Extension management icon
    └── icon-128.png    # Chrome Web Store icon
```

No tests for this project — it's a ~280-line browser extension with no build step, tested manually by loading as unpacked extension.

---

## Task 1: Scaffold — manifest.json + icons

**Files:**
- Create: `chrome-extension/manifest.json`
- Create: `chrome-extension/icons/` (placeholder PNGs)

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p chrome-extension/icons
```

- [ ] **Step 2: Create manifest.json**

Create `chrome-extension/manifest.json`:

```json
{
  "manifest_version": 3,
  "name": "MediaHub Push",
  "version": "1.0.0",
  "description": "One-click push video URLs to MediaHub for parsing and download.",
  "permissions": [
    "storage",
    "contextMenus",
    "activeTab",
    "scripting"
  ],
  "host_permissions": [
    "<all_urls>"
  ],
  "action": {
    "default_popup": "popup.html",
    "default_icon": {
      "16": "icons/icon-16.png",
      "48": "icons/icon-48.png",
      "128": "icons/icon-128.png"
    }
  },
  "background": {
    "service_worker": "background.js"
  },
  "commands": {
    "push-to-mediahub": {
      "suggested_key": {
        "default": "Alt+M"
      },
      "description": "Push current page to MediaHub"
    }
  },
  "icons": {
    "16": "icons/icon-16.png",
    "48": "icons/icon-48.png",
    "128": "icons/icon-128.png"
  }
}
```

Note: `scripting` permission is needed to programmatically inject `content.js` on demand.

- [ ] **Step 3: Create placeholder icons**

Generate simple colored-square PNG icons at 16x16, 48x48, 128x128. Use any method:
- Simple canvas-generated PNGs, or
- Copy from an icon generator

The icons should use MediaHub's brand color (blue/teal) with an "M" letter.

- [ ] **Step 4: Commit**

```bash
git add chrome-extension/
git commit -m "feat(extension): scaffold manifest.json and icons"
```

---

## Task 2: Popup — Configuration UI

**Files:**
- Create: `chrome-extension/popup.html`
- Create: `chrome-extension/popup.css`
- Create: `chrome-extension/popup.js`

- [ ] **Step 1: Create popup.html**

Create `chrome-extension/popup.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <link rel="stylesheet" href="popup.css">
</head>
<body>
  <div class="container">
    <h1>MediaHub Push</h1>
    <div class="field">
      <label for="apiUrl">API URL</label>
      <input type="url" id="apiUrl" placeholder="https://mediahub.heygo.cn">
    </div>
    <div class="field">
      <label for="apiKey">API Key</label>
      <input type="password" id="apiKey" placeholder="dk_...">
    </div>
    <button id="saveBtn">Save</button>
    <div id="status" class="status"></div>
  </div>
  <script src="popup.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create popup.css**

Create `chrome-extension/popup.css`:

```css
* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  width: 320px;
  background: #1a1a2e;
  color: #e0e0e0;
}

.container {
  padding: 20px;
}

h1 {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 16px;
  color: #ffffff;
}

.field {
  margin-bottom: 12px;
}

label {
  display: block;
  font-size: 12px;
  color: #a0a0b0;
  margin-bottom: 4px;
}

input {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid #333355;
  border-radius: 6px;
  background: #16213e;
  color: #e0e0e0;
  font-size: 13px;
  outline: none;
}

input:focus {
  border-color: #4a9eff;
}

button {
  width: 100%;
  padding: 9px;
  border: none;
  border-radius: 6px;
  background: #4a9eff;
  color: #ffffff;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  margin-top: 4px;
}

button:hover {
  background: #3a8eef;
}

.status {
  margin-top: 10px;
  font-size: 12px;
  text-align: center;
  min-height: 18px;
}

.status.success {
  color: #4caf50;
}

.status.error {
  color: #f44336;
}

.status.info {
  color: #a0a0b0;
}
```

- [ ] **Step 3: Create popup.js**

Create `chrome-extension/popup.js`:

```js
const apiUrlInput = document.getElementById('apiUrl');
const apiKeyInput = document.getElementById('apiKey');
const saveBtn = document.getElementById('saveBtn');
const statusEl = document.getElementById('status');

// Load saved config on open
chrome.storage.sync.get(['apiUrl', 'apiKey'], (result) => {
  if (result.apiUrl) apiUrlInput.value = result.apiUrl;
  if (result.apiKey) apiKeyInput.value = result.apiKey;
  updateStatus(result.apiUrl && result.apiKey);
});

// Save config
saveBtn.addEventListener('click', () => {
  const apiUrl = apiUrlInput.value.trim().replace(/\/+$/, '');
  const apiKey = apiKeyInput.value.trim();

  if (!apiUrl) {
    showMessage('API URL is required', 'error');
    return;
  }
  if (!apiKey) {
    showMessage('API Key is required', 'error');
    return;
  }

  chrome.storage.sync.set({ apiUrl, apiKey }, () => {
    showMessage('Saved!', 'success');
    updateStatus(true);
  });
});

function showMessage(text, type) {
  statusEl.textContent = text;
  statusEl.className = 'status ' + type;
  if (type === 'success') {
    setTimeout(() => updateStatus(true), 2000);
  }
}

function updateStatus(configured) {
  if (configured) {
    statusEl.textContent = 'Connected';
    statusEl.className = 'status success';
  } else {
    statusEl.textContent = 'Not configured';
    statusEl.className = 'status info';
  }
}
```

- [ ] **Step 4: Test manually**

1. Open `chrome://extensions/`
2. Enable Developer mode
3. Click "Load unpacked" → select `chrome-extension/` directory
4. Click extension icon → popup should appear with two input fields
5. Enter URL + Key → click Save → should show "Saved!"
6. Close and re-open popup → values should persist

- [ ] **Step 5: Commit**

```bash
git add chrome-extension/popup.*
git commit -m "feat(extension): add popup config UI"
```

---

## Task 3: Background Service Worker — Push Logic

**Files:**
- Create: `chrome-extension/background.js`

- [ ] **Step 1: Create background.js**

Create `chrome-extension/background.js`:

```js
// Register context menu on install
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'push-to-mediahub',
    title: 'Push to MediaHub',
    contexts: ['page'],
  });
});

// Handle context menu click
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === 'push-to-mediahub' && tab?.url) {
    pushUrl(tab.id, tab.url);
  }
});

// Handle keyboard shortcut
chrome.commands.onCommand.addListener((command) => {
  if (command === 'push-to-mediahub') {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs[0];
      if (tab?.url) {
        pushUrl(tab.id, tab.url);
      }
    });
  }
});

async function pushUrl(tabId, url) {
  // Show "pushing" toast
  await showToast(tabId, 'Pushing to MediaHub...', 'info');

  // Read config
  const config = await chrome.storage.sync.get(['apiUrl', 'apiKey']);
  if (!config.apiUrl || !config.apiKey) {
    await showToast(tabId, 'Not configured. Click extension icon to set up.', 'error');
    return;
  }

  try {
    const response = await fetch(`${config.apiUrl}/api/v1/videos/fetch`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': config.apiKey,
      },
      body: JSON.stringify({
        url,
        video_bool: true,
        cover_bool: true,
      }),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
      const detail = typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail);
      await showToast(tabId, `Push failed: ${detail}`, 'error');
      return;
    }

    const data = await response.json();
    if (data.success) {
      await showToast(tabId, 'Pushed! Parsing started.', 'success');
    } else {
      await showToast(tabId, `Push failed: ${data.message || 'Unknown error'}`, 'error');
    }
  } catch (err) {
    await showToast(tabId, `Push failed: ${err.message}`, 'error');
  }
}

async function showToast(tabId, message, type) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ['content.js'],
    });
  } catch {
    // content script may already be injected
  }
  try {
    await chrome.tabs.sendMessage(tabId, { action: 'showToast', message, type });
  } catch {
    // tab may not be scriptable (chrome:// pages)
  }
}
```

- [ ] **Step 2: Test manually**

1. Reload extension in `chrome://extensions/`
2. Navigate to any video page (e.g., douyin.com)
3. Right-click → "Push to MediaHub" should appear
4. Click it → should see toast (will fail if API not configured, which is expected)
5. Test `Alt+M` shortcut

- [ ] **Step 3: Commit**

```bash
git add chrome-extension/background.js
git commit -m "feat(extension): add background service worker with push logic"
```

---

## Task 4: Content Script — Toast Notifications

**Files:**
- Create: `chrome-extension/content.js`

- [ ] **Step 1: Create content.js**

Create `chrome-extension/content.js`:

```js
// Avoid duplicate injection
if (!window.__mediahubToastInjected) {
  window.__mediahubToastInjected = true;

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.action === 'showToast') {
      showToast(msg.message, msg.type);
    }
  });
}

function showToast(message, type) {
  // Remove existing toast if any
  const existing = document.getElementById('mediahub-toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.id = 'mediahub-toast';

  const colors = {
    success: { bg: '#1b5e20', border: '#4caf50' },
    error: { bg: '#b71c1c', border: '#f44336' },
    info: { bg: '#0d47a1', border: '#2196f3' },
  };
  const c = colors[type] || colors.info;

  Object.assign(toast.style, {
    position: 'fixed',
    top: '16px',
    right: '16px',
    zIndex: '2147483647',
    padding: '12px 20px',
    borderRadius: '8px',
    background: c.bg,
    border: `1px solid ${c.border}`,
    color: '#ffffff',
    fontSize: '14px',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
    transition: 'opacity 0.3s ease',
    opacity: '0',
    maxWidth: '360px',
    wordBreak: 'break-word',
  });

  toast.textContent = message;
  document.body.appendChild(toast);

  // Fade in
  requestAnimationFrame(() => {
    toast.style.opacity = '1';
  });

  // Auto dismiss
  const delay = type === 'error' ? 5000 : 3000;
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, delay);
}
```

- [ ] **Step 2: Test full flow end-to-end**

1. Reload extension in `chrome://extensions/`
2. Click icon → enter API URL (`https://mediahub.heygo.cn`) + API Key → Save
3. Navigate to a Douyin video page
4. Press `Alt+M` or right-click → "Push to MediaHub"
5. Should see blue "Pushing to MediaHub..." toast
6. Then green "Pushed! Parsing started." or red error toast
7. Check MediaHub dashboard to verify the video was added

- [ ] **Step 3: Commit**

```bash
git add chrome-extension/content.js
git commit -m "feat(extension): add content script toast notifications"
```

---

## Task 5: Final Polish + README

**Files:**
- Create: `chrome-extension/README.md`

- [ ] **Step 1: Create README**

Create `chrome-extension/README.md`:

```markdown
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
```

- [ ] **Step 2: Commit all**

```bash
git add chrome-extension/
git commit -m "docs(extension): add README with install and usage instructions"
```
