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
  const config = await chrome.storage.local.get(['apiUrl', 'apiKey']);
  if (!config.apiUrl || !config.apiKey) {
    await showToast(tabId, 'Not configured. Click extension icon to set up.', 'error');
    return;
  }

  try {
    const response = await fetch(`${config.apiUrl}/api/v1/media/fetch`, {
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
