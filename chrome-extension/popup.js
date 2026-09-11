// Pure helpers from intents.js (loaded before this file in popup.html).
const {
  PIPELINE_TAG_GROUP,
  DEFAULT_OPTIONS,
  applyIntentDependencies,
  buildIntentFields,
  filterPickableTags,
  describeErrorBody,
} = globalThis.MediaHubIntents;

// Elements — Settings
const settingsView = document.getElementById('settingsView');
const apiUrlInput = document.getElementById('apiUrl');
const apiKeyInput = document.getElementById('apiKey');
const webUrlInput = document.getElementById('webUrl');
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
const starButtons = Array.from(document.querySelectorAll('#pushOptions .star-btn'));
const intentButtons = Array.from(document.querySelectorAll('#pushOptions .intent-btn'));

// Elements — search-or-create bar (static, outside the re-rendered tag list)
const quickCreateBar = document.getElementById('quickCreateBar');
const quickCreateBtn = document.getElementById('quickCreateBtn');
const quickTranslateLabel = document.getElementById('quickTranslateLabel');
const quickTranslateInput = document.getElementById('quickTranslateInput');
const quickTranslateSameBtn = document.getElementById('quickTranslateSameBtn');
const quickCreateErrorEl = document.getElementById('quickCreateError');

let selectedTags = new Set();
let allTags = [];
// Real tag groups from GET /tags/groups. The "+" form's group dropdown used to
// reverse-engineer groups out of allTags, so a group with zero tags (a freshly
// created one — exactly when you want to file something into it) never
// appeared as an option.
let allGroups = [];
let currentTabUrl = '';
let tagQuery = '';
// Search-or-create state: an in-flight quick-create keeps the affordance in a
// disabled "Creating..." state; a failure surfaces inline (never silent).
let isCreatingQuick = false;
let quickCreateError = '';
// Search-or-create translation state. `quickTranslateTouched` stops a late
// auto-translate response (or a re-render) from stomping on what the user
// typed or on an "=" they just pressed.
let quickTranslateTouched = false;
let quickTranslateQuery = '';
let quickTranslateTimer = null;
// Did the tag list actually load? `allTags` is [] both when the user has no
// tags and when the fetch failed, and the two must not behave alike: create-on-
// push keys on "the search found nothing", which is only a real signal if we
// hold the real list. On a failed load every query looks like zero results.
let tagsLoaded = false;
// Rating + AI intents for the next push. Replaced (never mutated) through
// setPushOption so the Summary/Analyze → Transcribe dependency always holds.
let pushOptions = DEFAULT_OPTIONS;

// Show the extension version beside the header — read at runtime from the
// manifest so it never drifts from manifest.json.
//
// Prefer version_name: scripts/package-extension.sh stamps it onto the COPY in
// release/ as "1.4.0 (aefb817e)" so the popup says exactly which commit is
// installed. The fallback matters — loading this folder directly (the debug
// path) has no version_name, and must show "v1.4.0", never "vundefined".
const versionEl = document.getElementById('appVersion');
if (versionEl && chrome.runtime && chrome.runtime.getManifest) {
  const manifest = chrome.runtime.getManifest();
  versionEl.textContent = 'v' + (manifest.version_name || manifest.version);
}

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
  document.getElementById('scanView').style.display = 'none';
  document.getElementById('modeTabs').style.display = 'none';
  document.body.classList.remove('scan-mode');
  chrome.storage.local.get(['apiUrl', 'apiKey', 'webUrl'], (result) => {
    apiUrlInput.value = result.apiUrl || 'https://cn.nous.ink:88';
    if (result.apiKey) apiKeyInput.value = result.apiKey;
    webUrlInput.value = result.webUrl || 'https://app.nous.ink';
  });
}

saveBtn.addEventListener('click', () => {
  const apiUrl = apiUrlInput.value.trim().replace(/\/+$/, '');
  const apiKey = apiKeyInput.value.trim();
  // Optional — used only for the "Open in nous" / "Generate Similar" deep
  // links from the prompt-analyze panel, so it falls back rather than
  // blocking Save the way apiUrl/apiKey do.
  const webUrl = (webUrlInput.value.trim() || 'https://app.nous.ink').replace(/\/+$/, '');

  if (!apiUrl) { showMessage('API URL is required', 'error'); return; }
  if (!apiKey) { showMessage('API Key is required', 'error'); return; }

  chrome.storage.local.set({ apiUrl, apiKey, webUrl }, () => {
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
  document.getElementById('modeTabs').style.display = 'flex';
  // Re-entering from Settings always lands on the Push tab — keep the tab
  // indicator and the scan view in sync with that.
  document.getElementById('scanView').style.display = 'none';
  document.getElementById('tabPush').classList.add('active');
  document.getElementById('tabScan').classList.remove('active');
  document.body.classList.remove('scan-mode');

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
    tagsLoaded = true;

    renderTags(allTags);
  } catch (err) {
    tagsLoading.textContent = `Failed to load tags: ${err.message}`;
    tagsLoading.style.color = '#f87171';
    return;
  }

  tagsLoading.style.display = 'none';
  // Non-blocking: the group list only feeds the create form's dropdown, so a
  // failure there must not take the tag picker down with it.
  loadGroups(config);
}

// Real group list for the create form's dropdown. Empty groups included —
// that's the whole point (see the allGroups declaration).
async function loadGroups(config) {
  try {
    const res = await fetch(`${config.apiUrl}/api/v1/tags/groups`, {
      headers: { 'X-API-Key': config.apiKey },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    allGroups = data.groups || [];
  } catch (err) {
    // Fall back to the old tag-derived list rather than showing no groups at
    // all; populateGroupDropdown handles the empty case.
    console.warn('[nous] failed to load tag groups:', err.message);
    allGroups = [];
  }
  populateGroupDropdown();
}

// Re-render on every keystroke — the tag list is small, no debounce needed.
// Editing the query dismisses any stale quick-create error.
tagSearch.addEventListener('input', () => {
  tagQuery = tagSearch.value;
  quickCreateError = '';
  // A new term invalidates the old counterpart, including a hand-typed one —
  // otherwise "ComfyUI" + "=" would leave "ComfyUI" glued to whatever you
  // type next.
  resetQuickTranslate();
  renderTags(allTags);
});

function resetQuickTranslate() {
  if (quickTranslateTimer) clearTimeout(quickTranslateTimer);
  quickTranslateTouched = false;
  quickTranslateQuery = '';
  quickTranslateInput.value = '';
}

// Enter in the search box creates the typed tag when it matches nothing
// exactly — the same fast path the "Create" affordance offers. The
// isComposing guard keeps an IME confirmation Enter (Chinese input) from
// firing a create (repo convention from #1442/#1453; plain DOM → read
// event.isComposing directly).
tagSearch.addEventListener('keydown', (e) => {
  if (e.isComposing) return;
  if (e.key !== 'Enter') return;
  const query = tagSearch.value.trim();
  if (query && !hasExactMatch(query)) {
    e.preventDefault();
    quickCreateTag(query, quickTranslateInput.value.trim());
  }
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

// Exact-match check for search-or-create: does any loaded tag already equal the
// query by English name OR Chinese name_zh (case-insensitive)? Mirrors the web
// app's TagsSettings noExactMatch rule — the backend 409s on a duplicate name,
// so an exact match must suppress the Create affordance. Empty query counts as
// "matched" so a blank search never offers Create.
//
// Deliberately checks ALL tags, hidden Pipeline ones included: this is a
// duplicate-name guard, not a search, and the server's name check covers
// system tags — offering "Create Summary" would only earn a 409.
function isExactNameMatch(tag, q) {
  return (tag.name || '').toLowerCase() === q || (tag.name_zh || '').toLowerCase() === q;
}

function hasExactMatch(query) {
  const q = (query || '').trim().toLowerCase();
  if (!q) return true;
  return allTags.some((tag) => isExactNameMatch(tag, q));
}

// Does the query name a hidden Pipeline tag exactly ("Summary", "总结")? Lets
// the empty state and Push point at the toggles instead of a dead end.
function namesPipelineTag(query) {
  const q = (query || '').trim().toLowerCase();
  if (!q) return false;
  return allTags.some((tag) => tag.group_name === PIPELINE_TAG_GROUP && isExactNameMatch(tag, q));
}

function renderTags(tags) {
  tagsContainer.innerHTML = '';

  const rawQuery = tagQuery.trim();
  const q = rawQuery.toLowerCase();
  // Pipeline tags never list, search, or rank (Frequently Used derives from
  // `filtered` too) — the toggles above Push carry those intents.
  const filtered = filterPickableTags(tags).filter(tag => matchesQuery(tag, q));

  // Search-or-create: when the query matches no tag exactly, offer a
  // "Create <query>" affordance. It sits above the results and fully replaces
  // the "No tags match" empty state — mirroring the web app's Settings
  // behavior. Enter in the search box triggers the same create.
  const showCreate = rawQuery.length > 0 && !hasExactMatch(rawQuery);
  syncQuickCreateBar(showCreate ? rawQuery : '');

  if (filtered.length === 0) {
    if (!showCreate) {
      const empty = document.createElement('div');
      empty.className = 'tags-empty';
      empty.textContent = namesPipelineTag(rawQuery)
        ? `"${rawQuery}" is an AI option now: use the toggles below`
        : q ? 'No tags match your search' : 'No tags yet';
      tagsContainer.appendChild(empty);
    }
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

// Drive the static search-or-create bar. `query` is '' when the bar should be
// hidden (blank search, or the query already exists as a tag).
//
// The bar carries a second line — "EN:"/"ZH:" + editable translation + "=" —
// so a proper noun stays identical in both languages. Without it, creating
// "ComfyUI" from the search box left name_zh null (and typing a Chinese term
// stuffed Chinese into the English `name` column), which is precisely what
// the "+" form's "=" button already avoided.
function syncQuickCreateBar(query) {
  if (!query) {
    quickCreateBar.style.display = 'none';
    if (quickTranslateTimer) clearTimeout(quickTranslateTimer);
    quickTranslateQuery = '';
    return;
  }

  quickCreateBar.style.display = 'flex';
  quickCreateBtn.disabled = isCreatingQuick;
  quickCreateBtn.textContent = isCreatingQuick
    ? `Creating "${query}"...`
    : `Create "${query}"`;
  quickTranslateLabel.textContent = isChinese(query) ? 'EN:' : 'ZH:';

  quickCreateErrorEl.style.display = quickCreateError ? 'block' : 'none';
  quickCreateErrorEl.textContent = quickCreateError;

  // New query → drop the previous suggestion and ask for a fresh one. An
  // edited/"="-ed value is the user's, so it survives.
  if (query !== quickTranslateQuery) {
    quickTranslateQuery = query;
    if (!quickTranslateTouched) {
      quickTranslateInput.value = '';
      scheduleQuickTranslate(query);
    }
  }
}

function scheduleQuickTranslate(query) {
  if (quickTranslateTimer) clearTimeout(quickTranslateTimer);
  quickTranslateTimer = setTimeout(async () => {
    const translated = await fetchTranslation(query);
    // Bail if the user moved on or typed their own in the meantime.
    if (!translated) return;
    if (quickTranslateTouched) return;
    if (quickTranslateQuery !== query) return;
    quickTranslateInput.value = translated;
  }, 600);
}

quickTranslateInput.addEventListener('input', () => {
  quickTranslateTouched = true;
});

// "=" — force the other language to match the input verbatim (ComfyUI, Lora,
// LLM… terms that shouldn't be translated at all).
quickTranslateSameBtn.addEventListener('click', () => {
  quickTranslateTouched = true;
  quickTranslateInput.value = tagSearch.value.trim();
  quickTranslateInput.focus();
});

quickCreateBtn.addEventListener('click', () => {
  quickCreateTag(tagSearch.value.trim(), quickTranslateInput.value.trim());
});

// Split one typed term plus its counterpart into the {name, name_zh} pair the
// API wants. Chinese input → the term IS name_zh and the counterpart is the
// English name; otherwise the term is the English name. A blank counterpart
// falls back to the term itself, so a tag never lands with a null side that
// the UI would then render as the other language.
function splitNamePair(term, counterpart) {
  const other = (counterpart || '').trim();
  return isChinese(term)
    ? { name: other || term, name_zh: term }
    : { name: term, name_zh: other || null };
}

// Search-or-create fast path: POST /tags, then select the new tag and clear
// the search so it surfaces as a selected pill. `counterpart` is the other
// language from the create bar ('' → English-only tag, same as before).
// Returns the created tag on success, null otherwise, so callers that need to
// branch on the outcome (the push handler) can tell "created" from "failed"
// instead of guessing from quickCreateError.
async function quickCreateTag(rawQuery, counterpart = '') {
  const term = (rawQuery || '').trim();
  if (!term || isCreatingQuick) return null;
  const { name, name_zh } = splitNamePair(term, counterpart);

  isCreatingQuick = true;
  quickCreateError = '';
  renderTags(allTags); // reflect the disabled "Creating..." state

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
      let detail;
      if (res.status === 403) {
        // dk_ API key without the "Manage Tags" (tags:write) scope — the
        // create endpoint rejects it. Point the user at the fix instead of
        // showing a bare 403.
        detail = 'API key lacks Manage Tags scope';
      } else {
        detail = await readErrorMessage(res);
      }
      throw new Error(detail);
    }

    const created = await res.json();
    isCreatingQuick = false;
    // Optimistic append + immediate selection, then clear the search so the
    // new (uncategorized) tag surfaces as a selected pill.
    allTags = [...allTags, created];
    if (created.id) selectedTags.add(created.id);
    updatePushBtn();
    tagQuery = '';
    tagSearch.value = '';
    resetQuickTranslate();
    renderTags(allTags);
    return created;
  } catch (err) {
    isCreatingQuick = false;
    quickCreateError = err.message;
    renderTags(allTags);
    return null;
  }
}

function updatePushBtn() {
  const count = selectedTags.size;
  pushBtn.textContent = count > 0
    ? `Push to MediaHub (${count} tags)`
    : 'Push to MediaHub';
}

// --- Processing options (rating + AI intents) ---
// Static markup in popup.html; this only keeps lit / aria-pressed in sync.
function renderPushOptions() {
  for (const btn of starButtons) {
    const n = Number(btn.dataset.rating);
    const lit = pushOptions.rating !== null && n <= pushOptions.rating;
    btn.classList.toggle('lit', lit);
    btn.setAttribute('aria-pressed', String(lit));
  }
  for (const btn of intentButtons) {
    const on = pushOptions[btn.dataset.intent] === true;
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-pressed', String(on));
  }
}

function setPushOption(key, value) {
  pushOptions = applyIntentDependencies(pushOptions, key, value);
  renderPushOptions();
}

for (const btn of starButtons) {
  btn.addEventListener('click', () => {
    const n = Number(btn.dataset.rating);
    // Tapping the current rating again clears it — there is no "none" chip.
    setPushOption('rating', pushOptions.rating === n ? null : n);
  });
}

for (const btn of intentButtons) {
  btn.addEventListener('click', () => {
    const key = btn.dataset.intent;
    setPushOption(key, pushOptions[key] !== true);
  });
}

renderPushOptions();

// Response body → readable message (ErrorResponse envelope aware).
async function readErrorMessage(res) {
  const body = await res.json().catch(() => null);
  return describeErrorBody(body, res.status);
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

  // Search-or-create on push: a query that returned *no results at all* is text
  // the user typed meaning to tag with, so Push alone completes it — no separate
  // click on the "Create" affordance. Runs before the fetch so the new tag ships
  // in the same request (quickCreateTag selects it).
  //
  // Deliberately keyed on "zero matches", not on hasExactMatch: clicking a pill
  // leaves the query in the box, so typing "赛博", picking the offered
  // "赛博朋克", then pushing would otherwise also mint a junk "赛博" tag.
  // Any match at all means the user had real options and chose among them.
  // tagsLoaded gates the whole thing: after a failed tag fetch allTags is [],
  // so every query would read as "zero results" and Push would mint a tag that
  // may well already exist (409 → the push is blocked for no good reason).
  //
  // "Results" means what the list showed, so hidden Pipeline tags don't count;
  // but a query naming one exactly ("Summary") must not mint a tag either (the
  // server 409s on the name) — stop and point at the toggle instead.
  const pendingTag = tagSearch.value.trim();
  if (namesPipelineTag(pendingTag)) {
    pushStatus.textContent = `"${pendingTag}" is an AI option: use the toggles, then clear the search`;
    pushStatus.className = 'status error';
    pushBtn.disabled = false;
    updatePushBtn();
    return;
  }
  const pendingHasMatch = filterPickableTags(allTags)
    .some((t) => matchesQuery(t, pendingTag.toLowerCase()));
  if (tagsLoaded && pendingTag && !pendingHasMatch) {
    pushBtn.textContent = `Creating "${pendingTag}"...`;
    // Carry the create bar's counterpart through: if the user pressed "=" (or
    // typed a translation) and then went straight for Push, honouring it here
    // is the difference between "ComfyUI/ComfyUI" and a tag that silently
    // loses the half they just set.
    const created = await quickCreateTag(pendingTag, quickTranslateInput.value.trim());
    if (!created) {
      // Creating failed (403 missing tags:write, 409, network). Stop rather
      // than pushing without the tag the user asked for — a silent drop would
      // look like success while losing their intent.
      pushStatus.textContent =
        `Tag "${pendingTag}" failed: ${quickCreateError || 'unknown error'}`;
      pushStatus.className = 'status error';
      pushBtn.disabled = false;
      updatePushBtn();
      return;
    }
  }

  const config = await chrome.storage.local.get(['apiUrl', 'apiKey']);

  try {
    // Push URL with tags + processing options in a single request. Only set
    // options travel (rating when chosen, intents when on), so a push with
    // nothing set is byte-identical to pre-1.4.0.
    const fetchBody = {
      url: urlToPush,
      video_bool: true,
      cover_bool: true,
      ...(selectedTags.size > 0 ? { tag_ids: Array.from(selectedTags) } : {}),
      ...buildIntentFields(pushOptions),
    };

    const res = await fetch(`${config.apiUrl}/api/v1/media/fetch`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': config.apiKey,
      },
      body: JSON.stringify(fetchBody),
    });

    if (!res.ok) {
      throw new Error(await readErrorMessage(res));
    }

    // Tags and options deliberately stay as they are after a success (the
    // popup has never reset its selection here), so a follow-up push of
    // another URL reuses them.
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

  // Real groups first (includes empty ones). If /tags/groups failed, fall back
  // to the names reachable through the loaded tags so the dropdown degrades
  // instead of going blank.
  // The Pipeline group is excluded from both sources: its tags are hidden, so
  // filing a new tag there would make it vanish from the picker.
  const source = allGroups.length
    ? allGroups
        .filter((g) => g.name !== PIPELINE_TAG_GROUP)
        .map((g) => ({ id: String(g.id), name: g.name }))
    : filterPickableTags(allTags)
        .filter((t) => t.group_name && t.group_id)
        .map((t) => ({ id: String(t.group_id), name: t.group_name }));

  for (const group of source) {
    if (seen.has(group.id)) continue;
    seen.add(group.id);
    const opt = document.createElement('div');
    opt.className = 'custom-select-option';
    opt.textContent = group.name;
    opt.addEventListener('click', () => selectGroup(group.id, group.name));
    newTagGroupOptions.appendChild(opt);
  }
}

const isChinese = (text) => /[\u4e00-\u9fff]/.test(text);

// One-shot machine translation of `value` into the other language. Returns ''
// on any failure (offline, rate-limited, echoed input) \u2014 callers treat that as
// "no suggestion", never as an error worth surfacing.
async function fetchTranslation(value) {
  const term = (value || '').trim();
  if (!term) return '';
  try {
    const langPair = isChinese(term) ? 'zh|en' : 'en|zh';
    const res = await fetch(
      `https://api.mymemory.translated.net/get?q=${encodeURIComponent(term)}&langpair=${langPair}&de=8512939@qq.com`
    );
    if (!res.ok) return '';
    const data = await res.json();
    const translated = data?.responseData?.translatedText;
    return translated && translated !== term ? translated : '';
  } catch {
    return '';
  }
}

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
    const translated = await fetchTranslation(value);
    if (translated && !translateInput.value.trim()) {
      translateInput.value = translated;
    }
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

  const { name, name_zh } = splitNamePair(input, translateInput.value);
  const groupId = newTagGroup.value || null;

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
      // POST carries group_id directly. This used to be POST-then-PUT, and
      // that PUT both 500'd (str group_id into a BIGINT column) and was
      // wrapped in `.catch(() => {})` — so the tag was created outside the
      // chosen group while the UI still said "Created!".
      body: JSON.stringify({ name, name_zh, group_id: groupId }),
    });

    if (!res.ok) {
      throw new Error(await readErrorMessage(res));
    }

    const created = await res.json();
    // The group is part of what the user asked for — if the server filed the
    // tag somewhere else, say so instead of reporting a clean success.
    if (groupId && String(created.group_id ?? '') !== String(groupId)) {
      throw new Error('Tag created, but the group was not applied');
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
