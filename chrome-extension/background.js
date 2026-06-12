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

// ============================================================
// Scan-import pipeline (Scan Images mode)
//
// Runs here (not in the popup) so a batch import survives the popup
// closing. Images are fetched browser-side with site cookies — this
// reaches right-click-protected / auth-gated CDN images a server-side
// fetch can't — then uploaded as multipart to /resources/upload.
// ============================================================

const REFERER_RULE_ID = 9001;
const IMPORT_CONCURRENCY = 3;
const MAX_IMAGE_BYTES = 100 * 1024 * 1024; // 100 MB per image

// Single import job at a time; popup polls this via getImageImportState.
let importJob = null;

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.action === 'startImageImport') {
    if (importJob && (importJob.phase === 'importing' || importJob.phase === 'tagging')) {
      sendResponse({ ok: false, error: 'An import is already running' });
      return false;
    }
    runImageImport(msg.payload).catch((err) => {
      console.error('Image import crashed:', err);
    });
    sendResponse({ ok: true });
    return false;
  }
  if (msg.action === 'getImageImportState') {
    sendResponse({ job: importJob });
    return false;
  }
  return false;
});

async function runImageImport(payload) {
  const { images, pageUrl } = payload;
  importJob = {
    total: images.length,
    done: 0,
    failed: [],
    phase: 'importing',
    uploadedIds: [],
    tagDispatched: false,
    error: null,
  };
  broadcastJob();

  await setRefererRule(images, pageUrl);
  try {
    const queue = [...images];
    const worker = async () => {
      while (queue.length > 0) {
        const img = queue.shift();
        try {
          const resourceId = await importOneImage(img, payload);
          if (resourceId) importJob.uploadedIds.push(resourceId);
        } catch (err) {
          importJob.failed.push({ url: img.url, error: String(err?.message || err) });
        }
        importJob.done += 1;
        broadcastJob();
      }
    };
    await Promise.all(
      Array.from({ length: Math.min(IMPORT_CONCURRENCY, images.length) }, worker)
    );

    if (payload.autoTag && importJob.uploadedIds.length > 0) {
      importJob.phase = 'tagging';
      broadcastJob();
      importJob.tagDispatched = await dispatchAutoTag(
        importJob.uploadedIds, payload.apiUrl, payload.apiKey
      );
    }
    importJob.phase = 'done';
  } catch (err) {
    importJob.phase = 'error';
    importJob.error = String(err?.message || err);
  } finally {
    await clearRefererRule();
    broadcastJob();
  }
}

async function importOneImage(img, { apiUrl, apiKey, scopeId, folderId }) {
  // Browser-side fetch: site cookies ride along (host_permissions grant the
  // SameSite exemption) and the DNR session rule supplies the page Referer,
  // so referer-gated CDNs serve the real bytes.
  const res = await fetch(img.url, { credentials: 'include' });
  if (!res.ok) throw new Error(`Image fetch HTTP ${res.status}`);
  const blob = await res.blob();
  if (blob.size === 0) throw new Error('Empty image response');
  if (blob.size > MAX_IMAGE_BYTES) throw new Error('Image too large');

  // Reject obvious non-images (login/error pages). Octet-stream is allowed —
  // the backend sniffs the real MIME from content.
  const contentType = (blob.type || res.headers.get('content-type') || '').toLowerCase();
  if (contentType.startsWith('text/') || contentType.includes('json')) {
    throw new Error(`Not an image (${contentType})`);
  }

  const form = new FormData();
  form.append('file', blob, filenameForImage(img.url, contentType));

  const params = new URLSearchParams({ scope_id: scopeId });
  if (folderId) params.set('folder_id', folderId);

  const upload = await fetch(`${apiUrl}/api/v1/resources/upload?${params}`, {
    method: 'POST',
    headers: { 'X-API-Key': apiKey },
    body: form,
  });
  if (!upload.ok) {
    const err = await upload.json().catch(() => ({}));
    const detail = typeof err.detail === 'string' ? err.detail : `Upload HTTP ${upload.status}`;
    throw new Error(detail);
  }
  const json = await upload.json();
  return json?.data?.id ? String(json.data.id) : null;
}

const EXT_BY_MIME = {
  'image/jpeg': '.jpg',
  'image/png': '.png',
  'image/webp': '.webp',
  'image/gif': '.gif',
  'image/avif': '.avif',
  'image/svg+xml': '.svg',
  'image/bmp': '.bmp',
};

function filenameForImage(url, contentType) {
  let base = '';
  try {
    base = decodeURIComponent(new URL(url).pathname.split('/').pop() || '');
  } catch {
    base = '';
  }
  base = base.replace(/[^\w.\-]+/g, '_').slice(-80);
  if (!/\.[a-z0-9]{2,5}$/i.test(base)) {
    const ext = EXT_BY_MIME[contentType.split(';')[0].trim()] || '.jpg';
    base = (base || `image-${Date.now()}`) + ext;
  }
  return base;
}

async function dispatchAutoTag(resourceIds, apiUrl, apiKey) {
  // /resources/ai/batch caps at 50 per call
  let anyDispatched = false;
  for (let i = 0; i < resourceIds.length; i += 50) {
    const chunk = resourceIds.slice(i, i + 50);
    try {
      const res = await fetch(`${apiUrl}/api/v1/resources/ai/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
        body: JSON.stringify({ resource_ids: chunk, operation: 'classify' }),
      });
      if (res.ok) anyDispatched = true;
      else console.error(`Auto-tag batch HTTP ${res.status}`);
    } catch (err) {
      console.error('Auto-tag batch failed:', err);
    }
  }
  return anyDispatched;
}

// DNR session rule: stamp the scanned page's URL as Referer on our own
// XHR/fetch requests to the image hosts. Required for referer-gated CDNs
// (fetch() can't set Referer to a foreign origin directly).
async function setRefererRule(images, pageUrl) {
  if (!pageUrl) return;
  const domains = [
    ...new Set(
      images
        .map((i) => {
          try {
            return new URL(i.url).hostname;
          } catch {
            return null;
          }
        })
        .filter(Boolean)
    ),
  ];
  if (domains.length === 0) return;
  try {
    await chrome.declarativeNetRequest.updateSessionRules({
      removeRuleIds: [REFERER_RULE_ID],
      addRules: [
        {
          id: REFERER_RULE_ID,
          priority: 1,
          action: {
            type: 'modifyHeaders',
            requestHeaders: [
              { header: 'Referer', operation: 'set', value: pageUrl },
            ],
          },
          condition: {
            requestDomains: domains,
            resourceTypes: ['xmlhttprequest'],
          },
        },
      ],
    });
  } catch (err) {
    // Non-fatal: imports still work for non-referer-gated images
    console.error('Failed to set referer rule:', err);
  }
}

async function clearRefererRule() {
  try {
    await chrome.declarativeNetRequest.updateSessionRules({
      removeRuleIds: [REFERER_RULE_ID],
    });
  } catch {
    // already gone
  }
}

function broadcastJob() {
  // Popup may be closed — sendMessage rejects with "no receiver"; ignore.
  chrome.runtime.sendMessage({ action: 'imageImportProgress', job: importJob })
    .catch(() => {});
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
