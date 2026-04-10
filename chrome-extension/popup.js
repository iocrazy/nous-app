// Elements — Settings
const settingsView = document.getElementById('settingsView');
const apiUrlInput = document.getElementById('apiUrl');
const apiKeyInput = document.getElementById('apiKey');
const saveBtn = document.getElementById('saveBtn');
const statusEl = document.getElementById('status');

// Elements — Push
const pushView = document.getElementById('pushView');
const currentUrlEl = document.getElementById('currentUrl');
const tagsLoading = document.getElementById('tagsLoading');
const tagsContainer = document.getElementById('tagsContainer');
const pushBtn = document.getElementById('pushBtn');
const pushStatus = document.getElementById('pushStatus');
const settingsToggle = document.getElementById('settingsToggle');

let selectedTags = new Set();
let allTags = [];
let currentTabUrl = '';

// Init: check if configured, show appropriate view
chrome.storage.local.get(['apiUrl', 'apiKey'], (config) => {
  if (config.apiUrl && config.apiKey) {
    showPushView(config);
  } else {
    showSettingsView();
  }
});

// --- Settings View ---
function showSettingsView() {
  settingsView.style.display = 'block';
  pushView.style.display = 'none';
  chrome.storage.local.get(['apiUrl', 'apiKey'], (result) => {
    if (result.apiUrl) apiUrlInput.value = result.apiUrl;
    if (result.apiKey) apiKeyInput.value = result.apiKey;
  });
}

saveBtn.addEventListener('click', () => {
  const apiUrl = apiUrlInput.value.trim().replace(/\/+$/, '');
  const apiKey = apiKeyInput.value.trim();

  if (!apiUrl) { showMessage('API URL is required', 'error'); return; }
  if (!apiKey) { showMessage('API Key is required', 'error'); return; }

  chrome.storage.local.set({ apiUrl, apiKey }, () => {
    showMessage('Saved!', 'success');
    setTimeout(() => showPushView({ apiUrl, apiKey }), 800);
  });
});

settingsToggle.addEventListener('click', showSettingsView);

function showMessage(text, type) {
  statusEl.textContent = text;
  statusEl.className = 'status ' + type;
}

// --- Push View ---
async function showPushView(config) {
  settingsView.style.display = 'none';
  pushView.style.display = 'block';

  // Get current tab URL
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentTabUrl = tab?.url || '';
  currentUrlEl.textContent = currentTabUrl || 'No URL';
  currentUrlEl.title = currentTabUrl;

  // Load tags
  await loadTags(config);
}

async function loadTags(config) {
  tagsLoading.style.display = 'block';
  tagsContainer.innerHTML = '';

  try {
    const res = await fetch(`${config.apiUrl}/api/v1/tags?enabled_only=true`, {
      headers: { 'X-API-Key': config.apiKey },
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    allTags = data.tags || [];

    renderTags(allTags);
  } catch (err) {
    tagsLoading.textContent = `Failed to load tags: ${err.message}`;
    tagsLoading.style.color = '#f87171';
    return;
  }

  tagsLoading.style.display = 'none';
}

function renderTags(tags) {
  tagsContainer.innerHTML = '';

  // Group by group_name
  const groups = new Map();
  const ungrouped = [];
  for (const tag of tags) {
    if (!tag.group_name) { ungrouped.push(tag); continue; }
    if (!groups.has(tag.group_name)) groups.set(tag.group_name, []);
    groups.get(tag.group_name).push(tag);
  }

  // Append ungrouped to last group
  const entries = Array.from(groups.entries());
  if (ungrouped.length > 0 && entries.length > 0) {
    entries[entries.length - 1][1].push(...ungrouped);
  }

  // Frequently used (top 6)
  const top = [...tags]
    .filter(t => t.media_count > 0)
    .sort((a, b) => b.media_count - a.media_count)
    .slice(0, 6);

  if (top.length > 0) {
    const label = document.createElement('div');
    label.className = 'tag-group-label';
    label.innerHTML = '🔥 Frequently Used';
    tagsContainer.appendChild(label);
    top.forEach(tag => tagsContainer.appendChild(createTagPill(tag)));
  }

  // Groups
  for (const [groupName, groupTags] of entries) {
    const label = document.createElement('div');
    label.className = 'tag-group-label';
    label.textContent = groupName;
    tagsContainer.appendChild(label);
    groupTags.forEach(tag => tagsContainer.appendChild(createTagPill(tag)));
  }
}

function createTagPill(tag) {
  const pill = document.createElement('span');
  pill.className = 'tag-pill';
  pill.style.setProperty('--tag-color', tag.color || '#6366f1');

  const displayName = tag.name_zh || tag.name;
  pill.innerHTML = `${displayName} <span class="count">${tag.media_count ?? 0}</span>`;
  pill.dataset.tagId = tag.id;
  pill.dataset.tagName = displayName;

  if (selectedTags.has(tag.id)) pill.classList.add('selected');

  pill.addEventListener('click', () => {
    if (selectedTags.has(tag.id)) {
      selectedTags.delete(tag.id);
      pill.classList.remove('selected');
    } else {
      selectedTags.add(tag.id);
      pill.classList.add('selected');
    }
    updatePushBtn();
  });

  return pill;
}

function updatePushBtn() {
  const count = selectedTags.size;
  pushBtn.textContent = count > 0
    ? `Push to MediaHub (${count} tags)`
    : 'Push to MediaHub';
}

// --- Push Action ---
pushBtn.addEventListener('click', async () => {
  if (!currentTabUrl) {
    pushStatus.textContent = 'No URL to push';
    pushStatus.className = 'status error';
    return;
  }

  pushBtn.disabled = true;
  pushBtn.textContent = 'Pushing...';
  pushStatus.textContent = '';

  const config = await chrome.storage.local.get(['apiUrl', 'apiKey']);

  try {
    // 1. Push URL
    const res = await fetch(`${config.apiUrl}/api/v1/media/fetch`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': config.apiKey,
      },
      body: JSON.stringify({
        url: currentTabUrl,
        video_bool: true,
        cover_bool: true,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail));
    }

    const data = await res.json();

    // 2. If tags selected and we got a media ID, add tags
    if (selectedTags.size > 0 && data.media_id) {
      const tagIds = Array.from(selectedTags);
      await fetch(`${config.apiUrl}/api/v1/tags/media/${data.media_id}/tags`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': config.apiKey,
        },
        body: JSON.stringify({ tag_ids: tagIds }),
      });
    }

    pushStatus.textContent = 'Pushed!';
    pushStatus.className = 'status success';

    // Also show toast on the page
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab?.id) {
      try {
        await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['content.js'] });
      } catch {}
      try {
        await chrome.tabs.sendMessage(tab.id, {
          action: 'showToast',
          message: `Pushed! ${selectedTags.size > 0 ? `(${selectedTags.size} tags)` : ''}`,
          type: 'success',
        });
      } catch {}
    }
  } catch (err) {
    pushStatus.textContent = `Failed: ${err.message}`;
    pushStatus.className = 'status error';
  }

  pushBtn.disabled = false;
  updatePushBtn();
});

// --- Create Tag ---
const createTagToggle = document.getElementById('createTagToggle');
const createTagForm = document.getElementById('createTagForm');
const newTagInput = document.getElementById('newTagInput');
const translatePreview = document.getElementById('translatePreview');
const newTagGroup = document.getElementById('newTagGroup');
const newTagGroupTrigger = document.getElementById('newTagGroupTrigger');
const newTagGroupOptions = document.getElementById('newTagGroupOptions');
const createTagBtn = document.getElementById('createTagBtn');
const createTagStatus = document.getElementById('createTagStatus');

let translateTimer = null;

createTagToggle.addEventListener('click', () => {
  const visible = createTagForm.style.display !== 'none';
  createTagForm.style.display = visible ? 'none' : 'flex';
  createTagToggle.textContent = visible ? '+' : '×';
  if (!visible) {
    newTagInput.focus();
    populateGroupDropdown();
  }
});

// Custom select toggle
newTagGroupTrigger.addEventListener('click', () => {
  const open = newTagGroupOptions.style.display !== 'none';
  newTagGroupOptions.style.display = open ? 'none' : 'block';
});

// Close on outside click
document.addEventListener('click', (e) => {
  if (!document.getElementById('newTagGroupWrapper').contains(e.target)) {
    newTagGroupOptions.style.display = 'none';
  }
});

function selectGroup(value, label) {
  newTagGroup.value = value;
  newTagGroupTrigger.textContent = label;
  newTagGroupTrigger.classList.toggle('has-value', !!value);
  newTagGroupOptions.style.display = 'none';
}

function populateGroupDropdown() {
  const seen = new Set();
  newTagGroupOptions.innerHTML = '';

  // Default option
  const defaultOpt = document.createElement('div');
  defaultOpt.className = 'custom-select-option' + (!newTagGroup.value ? ' selected' : '');
  defaultOpt.textContent = 'Select group (optional)';
  defaultOpt.addEventListener('click', () => selectGroup('', 'Select group (optional)'));
  newTagGroupOptions.appendChild(defaultOpt);

  for (const tag of allTags) {
    if (tag.group_name && tag.group_id && !seen.has(tag.group_id)) {
      seen.add(tag.group_id);
      const opt = document.createElement('div');
      opt.className = 'custom-select-option';
      opt.textContent = tag.group_name;
      opt.addEventListener('click', () => selectGroup(tag.group_id, tag.group_name));
      newTagGroupOptions.appendChild(opt);
    }
  }
}

const isChinese = (text) => /[\u4e00-\u9fff]/.test(text);

newTagInput.addEventListener('input', () => {
  const value = newTagInput.value.trim();
  if (translateTimer) clearTimeout(translateTimer);
  if (!value) {
    translatePreview.style.display = 'none';
    return;
  }
  translateTimer = setTimeout(async () => {
    try {
      const langPair = isChinese(value) ? 'zh|en' : 'en|zh';
      const res = await fetch(
        `https://api.mymemory.translated.net/get?q=${encodeURIComponent(value)}&langpair=${langPair}&de=8512939@qq.com`
      );
      if (!res.ok) return;
      const data = await res.json();
      const translated = data?.responseData?.translatedText;
      if (translated && translated !== value) {
        translatePreview.textContent = `${isChinese(value) ? 'EN' : 'ZH'}: ${translated}`;
        translatePreview.style.display = 'block';
        translatePreview.dataset.translated = translated;
      }
    } catch {}
  }, 600);
});

createTagBtn.addEventListener('click', async () => {
  const input = newTagInput.value.trim();
  if (!input) return;

  const inputIsChinese = isChinese(input);
  const translated = translatePreview.dataset.translated || '';
  const name = inputIsChinese ? (translated || input) : input;
  const name_zh = inputIsChinese ? input : (translated || null);

  createTagBtn.disabled = true;
  createTagBtn.textContent = 'Creating...';
  createTagStatus.textContent = '';

  const config = await chrome.storage.local.get(['apiUrl', 'apiKey']);

  try {
    const res = await fetch(`${config.apiUrl}/api/v1/tags`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': config.apiKey,
      },
      body: JSON.stringify({ name, name_zh }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail));
    }

    // Set group_id if selected (PUT /tags/:id)
    const created = await res.json();
    const groupId = newTagGroup.value;
    if (groupId && created.id) {
      await fetch(`${config.apiUrl}/api/v1/tags/${created.id}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': config.apiKey,
        },
        body: JSON.stringify({ group_id: groupId }),
      }).catch(() => {});
    }

    // Reload tags
    await loadTags(config);
    newTagInput.value = '';
    translatePreview.style.display = 'none';
    translatePreview.dataset.translated = '';
    selectGroup('', 'Select group (optional)');
    createTagStatus.textContent = 'Created!';
    createTagStatus.className = 'status success';
    setTimeout(() => { createTagStatus.textContent = ''; }, 2000);
  } catch (err) {
    createTagStatus.textContent = err.message;
    createTagStatus.className = 'status error';
  }

  createTagBtn.disabled = false;
  createTagBtn.textContent = 'Create';
});
