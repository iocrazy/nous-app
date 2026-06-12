// Scan Images mode — scan the current page for images, preview + select,
// then import the selected ones into the MediaHub resource library.
// The actual fetch/upload pipeline runs in background.js so it survives
// the popup closing; this file only drives the UI.

// Elements — tabs
const modeTabs = document.getElementById('modeTabs');
const tabPush = document.getElementById('tabPush');
const tabScan = document.getElementById('tabScan');
const scanView = document.getElementById('scanView');
const pushViewEl = document.getElementById('pushView');

// Elements — scan view
const scopeSelect = document.getElementById('scopeSelect');
const folderSelect = document.getElementById('folderSelect');
const scanBtn = document.getElementById('scanBtn');
const scanStatus = document.getElementById('scanStatus');
const scanToolbar = document.getElementById('scanToolbar');
const selectAllChk = document.getElementById('selectAllChk');
const selectInfo = document.getElementById('selectInfo');
const minSizeSelect = document.getElementById('minSizeSelect');
const imageGrid = document.getElementById('imageGrid');
const autoTagRow = document.getElementById('autoTagRow');
const autoTagChk = document.getElementById('autoTagChk');
const importBtn = document.getElementById('importBtn');
const importProgress = document.getElementById('importProgress');
const progressFill = document.getElementById('progressFill');
const importStatus = document.getElementById('importStatus');

const MAX_SCAN_IMAGES = 300;

let scannedImages = [];
let selectedUrls = new Set();
let scopesLoaded = false;
let scanPageUrl = '';

// --- Tab switching ---
tabPush.addEventListener('click', () => {
  tabPush.classList.add('active');
  tabScan.classList.remove('active');
  scanView.style.display = 'none';
  pushViewEl.style.display = 'block';
  document.body.classList.remove('scan-mode');
});

tabScan.addEventListener('click', async () => {
  tabScan.classList.add('active');
  tabPush.classList.remove('active');
  pushViewEl.style.display = 'none';
  scanView.style.display = 'block';
  document.body.classList.add('scan-mode');
  if (!scopesLoaded) {
    await loadScopes();
  }
});

// --- Scope / folder pickers ---
async function getConfig() {
  return chrome.storage.local.get(['apiUrl', 'apiKey']);
}

async function loadScopes() {
  const config = await getConfig();
  try {
    const res = await fetch(`${config.apiUrl}/api/v1/teams`, {
      headers: { 'X-API-Key': config.apiKey },
    });
    if (!res.ok) {
      throw new Error(res.status === 403
        ? 'API key missing teams:read scope'
        : `HTTP ${res.status}`);
    }
    const data = await res.json();
    const teams = data.teams || [];
    scopeSelect.innerHTML = '';
    if (teams.length === 0) {
      scopeSelect.innerHTML = '<option value="">No scopes available</option>';
      return;
    }
    for (const team of teams) {
      const opt = document.createElement('option');
      opt.value = team.id;
      opt.textContent = team.kind === 'personal' ? `${team.name} (Personal)` : team.name;
      scopeSelect.appendChild(opt);
    }
    // Default to the personal scope when present
    const personal = teams.find((t) => t.kind === 'personal');
    if (personal) scopeSelect.value = personal.id;
    scopesLoaded = true;
    await loadFolders();
  } catch (err) {
    scopeSelect.innerHTML = '<option value="">Failed to load scopes</option>';
    setScanStatus(`Failed to load scopes: ${err.message}`, 'error');
  }
}

scopeSelect.addEventListener('change', loadFolders);

async function loadFolders() {
  const scopeId = scopeSelect.value;
  folderSelect.innerHTML = '<option value="">Library root</option>';
  if (!scopeId) return;

  const config = await getConfig();
  try {
    const res = await fetch(
      `${config.apiUrl}/api/v1/resources/folders/list?scope_id=${encodeURIComponent(scopeId)}`,
      { headers: { 'X-API-Key': config.apiKey } }
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    for (const folder of json.data || []) {
      const opt = document.createElement('option');
      opt.value = folder.id;
      opt.textContent = folder.name;
      folderSelect.appendChild(opt);
    }
  } catch (err) {
    setScanStatus(`Failed to load folders: ${err.message}`, 'error');
  }
}

// --- Page scan ---
// Runs inside the page (isolated world). Must be fully self-contained.
function collectPageImages(maxCount) {
  const seen = new Map();
  const HARD_MIN = 50; // drop obvious icons outright

  const add = (raw, w, h) => {
    if (!raw || seen.size >= maxCount) return;
    let abs;
    try {
      abs = new URL(raw, location.href);
    } catch {
      return;
    }
    if (abs.protocol !== 'http:' && abs.protocol !== 'https:') return;
    const prev = seen.get(abs.href);
    if (prev) {
      prev.w = Math.max(prev.w, w || 0);
      prev.h = Math.max(prev.h, h || 0);
      return;
    }
    seen.set(abs.href, { url: abs.href, w: w || 0, h: h || 0 });
  };

  const largestFromSrcset = (srcset) => {
    let best = null;
    let bestScore = -1;
    for (const part of srcset.split(',')) {
      const tokens = part.trim().split(/\s+/);
      if (!tokens[0]) continue;
      const desc = tokens[1] || '';
      let score = 0;
      if (desc.endsWith('w')) score = parseFloat(desc) || 0;
      else if (desc.endsWith('x')) score = (parseFloat(desc) || 0) * 1000;
      if (score >= bestScore) {
        bestScore = score;
        best = tokens[0];
      }
    }
    return best;
  };

  for (const img of document.querySelectorAll('img')) {
    const w = img.naturalWidth;
    const h = img.naturalHeight;
    if (w && h && (w < HARD_MIN || h < HARD_MIN)) continue;
    let url = img.currentSrc || img.src || '';
    // Lazy-load patterns: real URL parked in a data-* attribute while src
    // holds a placeholder.
    const lazy = img.dataset.src || img.dataset.original || img.dataset.lazySrc || '';
    if ((!url || url.startsWith('data:')) && lazy) url = lazy;
    if (img.srcset) {
      const fromSet = largestFromSrcset(img.srcset);
      if (fromSet) url = fromSet;
    }
    if (!url || url.startsWith('data:')) continue;
    add(url, w, h);
  }

  // CSS background images — bounded sweep so huge pages stay responsive.
  if (seen.size < maxCount) {
    const els = document.querySelectorAll('body *');
    const SWEEP_LIMIT = 5000;
    for (let i = 0; i < els.length && i < SWEEP_LIMIT && seen.size < maxCount; i++) {
      const bg = getComputedStyle(els[i]).backgroundImage;
      if (!bg || bg === 'none') continue;
      const m = bg.match(/url\(["']?([^"')]+)["']?\)/);
      if (!m || m[1].startsWith('data:')) continue;
      const rect = els[i].getBoundingClientRect();
      if (rect.width && rect.height && (rect.width < HARD_MIN || rect.height < HARD_MIN)) continue;
      add(m[1], Math.round(rect.width), Math.round(rect.height));
    }
  }

  return Array.from(seen.values());
}

scanBtn.addEventListener('click', async () => {
  scanBtn.disabled = true;
  scanBtn.textContent = 'Scanning...';
  setScanStatus('', 'info');

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) throw new Error('No active tab');
    scanPageUrl = tab.url || '';

    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: collectPageImages,
      args: [MAX_SCAN_IMAGES],
    });

    scannedImages = results?.[0]?.result || [];
    selectedUrls = new Set();
    if (scannedImages.length === 0) {
      setScanStatus('No images found on this page', 'info');
      scanToolbar.style.display = 'none';
      imageGrid.innerHTML = '';
      autoTagRow.style.display = 'none';
      importBtn.style.display = 'none';
    } else {
      setScanStatus(`Found ${scannedImages.length} images`, 'success');
      scanToolbar.style.display = 'flex';
      autoTagRow.style.display = 'flex';
      importBtn.style.display = 'block';
      renderGrid();
    }
  } catch (err) {
    setScanStatus(`Scan failed: ${err.message}`, 'error');
  }

  scanBtn.disabled = false;
  scanBtn.textContent = 'Scan Page Images';
});

// --- Grid rendering / selection ---
function visibleImages() {
  const minSize = parseInt(minSizeSelect.value, 10) || 0;
  // Images with unknown dimensions (0×0, e.g. background images) are kept —
  // filtering them out would silently hide real content.
  return scannedImages.filter(
    (img) => !img.w || !img.h || (img.w >= minSize && img.h >= minSize)
  );
}

function renderGrid() {
  const visible = visibleImages();
  const visibleSet = new Set(visible.map((i) => i.url));
  // Prune selection to what is actually displayed
  selectedUrls = new Set([...selectedUrls].filter((u) => visibleSet.has(u)));

  imageGrid.innerHTML = '';
  for (const img of visible) {
    const cell = document.createElement('div');
    cell.className = 'image-cell' + (selectedUrls.has(img.url) ? ' selected' : '');

    const thumb = document.createElement('img');
    thumb.src = img.url;
    thumb.loading = 'lazy';
    thumb.addEventListener('error', () => cell.classList.add('load-error'));

    const dims = document.createElement('div');
    dims.className = 'dims';
    dims.textContent = img.w && img.h ? `${img.w}×${img.h}` : '?';

    const mark = document.createElement('div');
    mark.className = 'checkmark';
    mark.textContent = '✓';

    cell.append(thumb, dims, mark);
    cell.addEventListener('click', () => {
      if (selectedUrls.has(img.url)) {
        selectedUrls.delete(img.url);
        cell.classList.remove('selected');
      } else {
        selectedUrls.add(img.url);
        cell.classList.add('selected');
      }
      updateSelectionInfo();
    });
    imageGrid.appendChild(cell);
  }
  updateSelectionInfo();
}

function updateSelectionInfo() {
  const visible = visibleImages();
  selectInfo.textContent = `${selectedUrls.size} / ${visible.length} selected`;
  selectAllChk.checked = visible.length > 0 && selectedUrls.size === visible.length;
  importBtn.disabled = selectedUrls.size === 0;
  importBtn.textContent = selectedUrls.size > 0
    ? `Import ${selectedUrls.size} Images`
    : 'Import Selected';
}

selectAllChk.addEventListener('change', () => {
  if (selectAllChk.checked) {
    selectedUrls = new Set(visibleImages().map((i) => i.url));
  } else {
    selectedUrls = new Set();
  }
  renderGrid();
});

minSizeSelect.addEventListener('change', renderGrid);

// --- Import ---
importBtn.addEventListener('click', async () => {
  const scopeId = scopeSelect.value;
  if (!scopeId) {
    setScanStatus('Pick a scope first', 'error');
    return;
  }
  const images = scannedImages.filter((i) => selectedUrls.has(i.url));
  if (images.length === 0) return;

  const config = await getConfig();
  const response = await chrome.runtime.sendMessage({
    action: 'startImageImport',
    payload: {
      images,
      pageUrl: scanPageUrl,
      scopeId,
      folderId: folderSelect.value || null,
      autoTag: autoTagChk.checked,
      apiUrl: config.apiUrl,
      apiKey: config.apiKey,
    },
  });

  if (!response?.ok) {
    setScanStatus(response?.error || 'Failed to start import', 'error');
    return;
  }
  importBtn.disabled = true;
  importProgress.style.display = 'block';
  renderJob({ total: images.length, done: 0, failed: [], phase: 'importing' });
});

// --- Progress (job runs in background.js) ---
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.action === 'imageImportProgress' && msg.job) {
    renderJob(msg.job);
  }
});

function renderJob(job) {
  importProgress.style.display = 'block';
  const pct = job.total ? Math.round((job.done / job.total) * 100) : 0;
  progressFill.style.width = `${pct}%`;

  const failedCount = job.failed?.length || 0;
  if (job.phase === 'importing') {
    importBtn.disabled = true;
    importStatus.textContent = `Importing ${job.done}/${job.total}` +
      (failedCount ? ` (${failedCount} failed)` : '');
    importStatus.className = 'status info';
  } else if (job.phase === 'tagging') {
    importBtn.disabled = true;
    importStatus.textContent = 'Dispatching AI auto-tag...';
    importStatus.className = 'status info';
  } else if (job.phase === 'done') {
    importBtn.disabled = selectedUrls.size === 0;
    const uploaded = job.total - failedCount;
    importStatus.textContent = `Done: ${uploaded} imported` +
      (failedCount ? `, ${failedCount} failed` : '') +
      (job.tagDispatched ? `, auto-tag dispatched` : '');
    importStatus.className = failedCount ? 'status error' : 'status success';
  } else if (job.phase === 'error') {
    importBtn.disabled = selectedUrls.size === 0;
    importStatus.textContent = `Import failed: ${job.error || 'unknown error'}`;
    importStatus.className = 'status error';
  }
}

function setScanStatus(text, type) {
  scanStatus.textContent = text;
  scanStatus.className = `status ${type}`;
}

// On popup open: if an import job is still running, jump to the scan tab so
// the user sees live progress.
(async () => {
  try {
    const state = await chrome.runtime.sendMessage({ action: 'getImageImportState' });
    if (state?.job && (state.job.phase === 'importing' || state.job.phase === 'tagging')) {
      // Defer past popup.js's async init (showPushView callback) so its
      // "land on Push tab" default doesn't override the switch.
      setTimeout(() => {
        tabScan.click();
        renderJob(state.job);
      }, 150);
    }
  } catch {
    // background not ready — fine
  }
})();
