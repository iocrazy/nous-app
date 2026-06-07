// Elements — Settings
const settingsView = document.getElementById('settingsView');
const apiUrlInput = document.getElementById('apiUrl');
const apiKeyInput = document.getElementById('apiKey');
const saveBtn = document.getElementById('saveBtn');
const statusEl = document.getElementById('status');

// Elements — Push
const pushView = document.getElementById('pushView');
const currentUrlEl = document.getElementById('currentUrl');
const tagSearch = document.getElementById('tagSearch');
const tagsLoading = document.getElementById('tagsLoading');
const tagsContainer = document.getElementById('tagsContainer');
const pushBtn = document.getElementById('pushBtn');
const pushStatus = document.getElementById('pushStatus');
const settingsToggle = document.getElementById('settingsToggle');

let selectedTags = new Set();
let allTags = [];
let currentTabUrl = '';
let tagQuery = '';

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
    apiUrlInput.value = result.apiUrl || 'https://mediahubserver.heygo.cn:88';
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

  // Pre-fill with current tab URL, but input is editable so user can paste anything
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentTabUrl = tab?.url || '';
  currentUrlEl.value = currentTabUrl;
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

// Re-render on every keystroke — the tag list is small, no debounce needed.
tagSearch.addEventListener('input', () => {
  tagQuery = tagSearch.value;
  renderTags(allTags);
});

// Most-used first within any list.
const byMediaCountDesc = (a, b) => (b.media_count ?? 0) - (a.media_count ?? 0);

// Filter by tag name (English + Chinese), case-insensitive.
function matchesQuery(tag, q) {
  if (!q) return true;
  const name = (tag.name || '').toLowerCase();
  const nameZh = (tag.name_zh || '').toLowerCase();
  return name.includes(q) || nameZh.includes(q);
}

function renderTags(tags) {
  tagsContainer.innerHTML = '';

  const q = tagQuery.trim().toLowerCase();
  const filtered = tags.filter(tag => matchesQuery(tag, q));

  if (filtered.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'tags-empty';
    empty.textContent = q ? 'No tags match your search' : 'No tags yet';
    tagsContainer.appendChild(empty);
    return;
  }

  // Group by group_name
  const groups = new Map();
  const ungrouped = [];
  for (const tag of filtered) {
    if (!tag.group_name) { ungrouped.push(tag); continue; }
    if (!groups.has(tag.group_name)) groups.set(tag.group_name, []);
    groups.get(tag.group_name).push(tag);
  }

  // Ungrouped tags always render as their own "未分类" section — never
  // tail-appended to another group (would visually bleed into it) and
  // never silently dropped (the old `entries.length > 0` guard did this
  // when tag_groups was empty in prod, costing 65 of 71 tags).
  const entries = Array.from(groups.entries());
  if (ungrouped.length > 0) {
    entries.push(['未分类', ungrouped]);
  }

  // Frequently used (top 6) — skipped while searching so results stay flat.
  if (!q) {
    const top = [...filtered]
      .filter(t => t.media_count > 0)
      .sort(byMediaCountDesc)
      .slice(0, 6);

    if (top.length > 0) {
      const label = document.createElement('div');
      label.className = 'tag-group-label';
      label.innerHTML = '🔥 Frequently Used';
      tagsContainer.appendChild(label);
      top.forEach(tag => tagsContainer.appendChild(createTagPill(tag)));
    }
  }

  // Groups — each sorted most-used first (immutable copy, no in-place mutation)
  for (const [groupName, groupTags] of entries) {
    const label = document.createElement('div');
    label.className = 'tag-group-label';
    label.textContent = groupName;
    tagsContainer.appendChild(label);
    [...groupTags]
      .sort(byMediaCountDesc)
      .forEach(tag => tagsContainer.appendChild(createTagPill(tag)));
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
  const urlToPush = (currentUrlEl.value || '').trim();
  if (!urlToPush) {
    pushStatus.textContent = 'No URL to push';
    pushStatus.className = 'status error';
    return;
  }

  pushBtn.disabled = true;
  pushBtn.textContent = 'Pushing...';
  pushStatus.textContent = '';

  const config = await chrome.storage.local.get(['apiUrl', 'apiKey']);

  try {
    // Push URL with tags in a single request
    const fetchBody = {
      url: urlToPush,
      video_bool: true,
      cover_bool: true,
    };
    if (selectedTags.size > 0) {
      fetchBody.tag_ids = Array.from(selectedTags);
    }

    const res = await fetch(`${config.apiUrl}/api/v1/media/fetch`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': config.apiKey,
      },
      body: JSON.stringify(fetchBody),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail));
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
const translateWrapper = document.getElementById('translateWrapper');
const translateLabel = document.getElementById('translateLabel');
const translateInput = document.getElementById('translateInput');
const translateSameBtn = document.getElementById('translateSameBtn');
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

// Show the translation row with a label matching the current direction.
// Kept in sync with the input direction even before auto-translate lands.
function showTranslateRow(value) {
  translateLabel.textContent = isChinese(value) ? 'EN:' : 'ZH:';
  translateWrapper.style.display = 'flex';
}

newTagInput.addEventListener('input', () => {
  const value = newTagInput.value.trim();
  if (translateTimer) clearTimeout(translateTimer);
  if (!value) {
    translateWrapper.style.display = 'none';
    translateInput.value = '';
    return;
  }
  showTranslateRow(value);
  // Don't wipe a value the user already edited — only auto-fill if the
  // translation input is currently empty.
  translateTimer = setTimeout(async () => {
    if (translateInput.value.trim()) return;
    try {
      const langPair = isChinese(value) ? 'zh|en' : 'en|zh';
      const res = await fetch(
        `https://api.mymemory.translated.net/get?q=${encodeURIComponent(value)}&langpair=${langPair}&de=8512939@qq.com`
      );
      if (!res.ok) return;
      const data = await res.json();
      const translated = data?.responseData?.translatedText;
      if (translated && translated !== value && !translateInput.value.trim()) {
        translateInput.value = translated;
      }
    } catch {}
  }, 600);
});

// "=" button — force translation to match input verbatim (for technical
// terms like Agent / Skill / LLM that should stay identical in both languages).
translateSameBtn.addEventListener('click', () => {
  translateInput.value = newTagInput.value.trim();
  translateInput.focus();
});

createTagBtn.addEventListener('click', async () => {
  const input = newTagInput.value.trim();
  if (!input) return;

  const inputIsChinese = isChinese(input);
  const translated = translateInput.value.trim();
  // If user left the translation blank, fall back to the input itself so the
  // tag still has a non-null name / name_zh rather than crashing.
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
    translateWrapper.style.display = 'none';
    translateInput.value = '';
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
