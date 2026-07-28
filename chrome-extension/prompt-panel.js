// Prompt-analyze floating panel — injected into the host page by
// background.js's "Analyze Prompt (nous)" context-menu handler. All network
// requests are delegated to background.js via chrome.runtime.sendMessage;
// this file only owns the DOM and the progress → UI state machine.

// Avoid duplicate injection (same guard pattern as content.js's toast).
if (!window.__nousPromptPanelInjected) {
  window.__nousPromptPanelInjected = true;

  // ============================================================
  // Pure functions — no DOM/chrome API access. Kept at the top so the
  // status-mapping and result-parsing logic stays readable/reviewable on
  // its own (this extension has no test harness to exercise it directly).
  // ============================================================

  const POLL_INTERVAL_MS = 2000;
  const POLL_TIMEOUT_MS = 90000;

  // Maps a task_tracking `status` value (queued/in_progress/completed/
  // failed/cancelled/lost — see CLAUDE.md's Task Center section) to the
  // panel phase + subtitle text to show while polling.
  function progressToPhase(status) {
    switch (status) {
      case 'completed':
        return { phase: 'completed', subtitle: 'Analysis complete' };
      case 'failed':
      case 'cancelled':
      case 'lost':
        return { phase: 'task-failed', subtitle: 'Analysis failed' };
      case 'queued':
        return { phase: 'polling', subtitle: 'Queued for analysis…' };
      case 'in_progress':
      default:
        return { phase: 'polling', subtitle: 'Analyzing image…' };
    }
  }

  // gen_prompt_json comes back from GET /resources/{id} as a JSON *string*
  // (or null/empty if the model reply didn't match the structured contract).
  // Tolerant parse: any failure degrades to "no structured data" rather than
  // breaking the result card.
  function parsePromptJson(raw) {
    if (!raw) return null;
    if (typeof raw === 'object') return raw;
    try {
      const obj = JSON.parse(raw);
      return obj && typeof obj === 'object' ? obj : null;
    } catch {
      return null;
    }
  }

  // Assembles the read-only view model the result card renders from —
  // isolated from DOM so the shape is easy to eyeball/change in one place.
  function buildResultView(resource, tagCount) {
    const json = parsePromptJson(resource?.gen_prompt_json);
    return {
      zh: resource?.gen_prompt_zh || '',
      en: resource?.gen_prompt || '',
      json,
      category: json?.category || null,
      aspectRatio: json?.aspect_ratio || null,
      tagCount: tagCount || 0,
    };
  }

  // ============================================================
  // Panel state + controller
  // ============================================================

  let panelEl = null;
  let state = null;

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.action === 'nousAnalyzePrompt') {
      startAnalysis(msg.imageUrl, msg.pageUrl);
    }
  });

  function sendBg(action, payload) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({ action, payload }, (response) => {
        if (chrome.runtime.lastError) {
          resolve({ ok: false, error: chrome.runtime.lastError.message });
          return;
        }
        resolve(response);
      });
    });
  }

  function ensurePanel() {
    if (panelEl) return panelEl;
    panelEl = document.createElement('div');
    panelEl.className = 'nous-prompt-panel';
    panelEl.innerHTML =
      '<div class="nous-pp-header">' +
      '<span class="nous-pp-title">Nous — Prompt Analysis</span>' +
      '<button type="button" class="nous-pp-close" aria-label="Close">×</button>' +
      '</div>' +
      '<div class="nous-pp-body"></div>';
    panelEl.querySelector('.nous-pp-close').addEventListener('click', closePanel);
    document.body.appendChild(panelEl);
    return panelEl;
  }

  function closePanel() {
    clearTimer();
    if (panelEl) {
      panelEl.remove();
      panelEl = null;
    }
    state = null;
  }

  function clearTimer() {
    if (state?.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  // Same page, repeated right-click-analyze: reuse the one panel instance
  // instead of stacking a second one.
  function startAnalysis(imageUrl, pageUrl) {
    ensurePanel();
    clearTimer();
    state = {
      imageUrl,
      pageUrl,
      resourceId: null,
      taskId: null,
      phase: 'uploading',
      subtitle: '',
      percent: 0,
      result: null,
      activeTab: null,
      pollTimer: null,
      pollStartedAt: 0,
    };
    runFlow();
  }

  function setPhase(phase, subtitle, percent) {
    if (!state) return;
    state.phase = phase;
    state.subtitle = subtitle;
    if (typeof percent === 'number') state.percent = percent;
    render();
  }

  async function runFlow() {
    if (!state) return;
    try {
      if (!state.resourceId) {
        setPhase('uploading', 'Uploading image…', 10);
        const uploadRes = await sendBg('promptPanelUpload', {
          imageUrl: state.imageUrl,
          pageUrl: state.pageUrl,
        });
        if (!state) return;
        if (!uploadRes?.ok) {
          setPhase('upload-failed', `Upload failed: ${uploadRes?.error || 'unknown error'}`);
          return;
        }
        state.resourceId = uploadRes.resourceId;
      }

      setPhase('dispatching', 'Starting analysis…', 20);
      const dispatchRes = await sendBg('promptPanelDispatch', { resourceId: state.resourceId });
      if (!state) return;
      if (!dispatchRes?.ok) {
        setPhase(
          'dispatch-failed',
          `Couldn't start analysis: ${dispatchRes?.error || 'unknown error'}`
        );
        return;
      }
      state.taskId = dispatchRes.taskId;
      startPolling();
    } catch (err) {
      if (!state) return;
      setPhase('dispatch-failed', `Unexpected error: ${String(err?.message || err)}`);
    }
  }

  function startPolling() {
    if (!state) return;
    setPhase('polling', 'Analyzing image…', 40);
    state.pollStartedAt = Date.now();
    state.pollTimer = setInterval(tick, POLL_INTERVAL_MS);
    tick();
  }

  async function tick() {
    if (!state || state.phase !== 'polling') return;

    if (Date.now() - state.pollStartedAt > POLL_TIMEOUT_MS) {
      clearTimer();
      setPhase(
        'timeout',
        'Still analyzing — this is taking longer than expected. You can retry, or check nous later.'
      );
      return;
    }

    const res = await sendBg('promptPanelProgress', { taskId: state.taskId });
    if (!state || state.phase !== 'polling') return; // state moved on while awaiting

    if (!res?.ok) {
      if (res?.status === 404) {
        clearTimer();
        setPhase('task-failed', `Analysis failed: ${res.error || 'task not found'}`);
      }
      // Any other error (network blip, 5xx) is treated as transient — keep
      // polling until the 90s timeout instead of failing on one bad tick.
      return;
    }

    const mapped = progressToPhase(res.data?.status);
    if (mapped.phase === 'completed') {
      clearTimer();
      await finishAndLoadResult();
    } else if (mapped.phase === 'task-failed') {
      clearTimer();
      setPhase('task-failed', `Analysis failed${res.data?.error ? `: ${res.data.error}` : '.'}`);
    } else {
      setPhase('polling', mapped.subtitle, Math.max(40, res.data?.percent || 0));
    }
  }

  async function finishAndLoadResult() {
    if (!state) return;
    setPhase('polling', 'Loading result…', 95);
    const [resourceRes, tagsRes] = await Promise.all([
      sendBg('promptPanelResource', { resourceId: state.resourceId }),
      sendBg('promptPanelTags', { resourceId: state.resourceId }),
    ]);
    if (!state) return;

    if (!resourceRes?.ok) {
      setPhase('task-failed', `Couldn't load the result: ${resourceRes?.error || 'unknown error'}`);
      return;
    }

    const tagCount = tagsRes?.ok && Array.isArray(tagsRes.data) ? tagsRes.data.length : 0;
    state.result = buildResultView(resourceRes.data, tagCount);
    state.activeTab = state.activeTab || 'en';
    setPhase('completed', null);
  }

  // ============================================================
  // Rendering
  // ============================================================

  function render() {
    if (!state || !panelEl) return;
    const body = panelEl.querySelector('.nous-pp-body');
    body.innerHTML = '';
    switch (state.phase) {
      case 'uploading':
      case 'dispatching':
      case 'polling':
        body.appendChild(renderProgress());
        break;
      case 'upload-failed':
      case 'dispatch-failed':
      case 'task-failed':
      case 'timeout':
        body.appendChild(renderError());
        break;
      case 'completed':
        body.appendChild(renderResult());
        break;
      default:
        break;
    }
  }

  function renderProgress() {
    const wrap = document.createElement('div');

    const track = document.createElement('div');
    track.className = 'nous-pp-progress-track';
    const fill = document.createElement('div');
    fill.className = 'nous-pp-progress-fill';
    fill.style.width = `${state.percent || 0}%`;
    track.appendChild(fill);

    const subtitle = document.createElement('div');
    subtitle.className = 'nous-pp-subtitle';
    subtitle.textContent = state.subtitle || 'Working…';

    wrap.append(track, subtitle);
    return wrap;
  }

  function renderError() {
    const wrap = document.createElement('div');

    const msg = document.createElement('div');
    msg.className = 'nous-pp-error';
    msg.textContent = state.subtitle || 'Something went wrong.';

    const retryBtn = document.createElement('button');
    retryBtn.type = 'button';
    retryBtn.className = 'nous-pp-btn primary';
    retryBtn.textContent = 'Retry';
    retryBtn.addEventListener('click', () => {
      clearTimer();
      runFlow();
    });

    wrap.append(msg, retryBtn);
    return wrap;
  }

  function renderResult() {
    const r = state.result;
    const wrap = document.createElement('div');

    if (r.category || r.aspectRatio) {
      const chips = document.createElement('div');
      chips.className = 'nous-pp-chips';
      if (r.category) chips.appendChild(makeChip(r.category));
      if (r.aspectRatio) chips.appendChild(makeChip(r.aspectRatio));
      wrap.appendChild(chips);
    }

    const tabsBar = document.createElement('div');
    tabsBar.className = 'nous-pp-tabs';
    const contentEl = document.createElement('div');
    contentEl.className = 'nous-pp-tab-content';

    const tabDefs = [
      { key: 'zh', label: '中文' },
      { key: 'en', label: 'EN' },
      { key: 'json', label: 'JSON' },
    ];

    function renderTabContent() {
      contentEl.textContent = '';
      if (state.activeTab === 'json') {
        const pre = document.createElement('pre');
        pre.style.margin = '0';
        pre.style.whiteSpace = 'pre-wrap';
        pre.style.wordBreak = 'break-word';
        pre.style.fontFamily = 'inherit';
        pre.textContent = r.json ? JSON.stringify(r.json, null, 2) : 'No structured data available.';
        contentEl.appendChild(pre);
      } else {
        const text = state.activeTab === 'zh' ? r.zh : r.en;
        contentEl.textContent = text || 'No prompt generated.';
      }
    }

    tabDefs.forEach((def) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'nous-pp-tab' + (def.key === state.activeTab ? ' active' : '');
      btn.textContent = def.label;
      btn.addEventListener('click', () => {
        state.activeTab = def.key;
        tabsBar.querySelectorAll('.nous-pp-tab').forEach((el) => el.classList.remove('active'));
        btn.classList.add('active');
        renderTabContent();
      });
      tabsBar.appendChild(btn);
    });
    renderTabContent();

    wrap.append(tabsBar, contentEl);

    if (r.tagCount > 0) {
      const tagsLine = document.createElement('div');
      tagsLine.className = 'nous-pp-tags-line';
      tagsLine.textContent = `${r.tagCount} tag${r.tagCount === 1 ? '' : 's'} saved to library`;
      wrap.appendChild(tagsLine);
    }

    const actions = document.createElement('div');
    actions.className = 'nous-pp-actions';

    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'nous-pp-btn';
    copyBtn.textContent = 'Copy Prompt';
    copyBtn.addEventListener('click', () => copyActiveTab(copyBtn));

    const similarBtn = document.createElement('button');
    similarBtn.type = 'button';
    similarBtn.className = 'nous-pp-btn';
    similarBtn.textContent = '⚡ Generate Similar';
    similarBtn.addEventListener('click', () => {
      sendBg('promptPanelOpen', { resourceId: state.resourceId, generateSimilar: true });
    });

    const openBtn = document.createElement('button');
    openBtn.type = 'button';
    openBtn.className = 'nous-pp-btn primary';
    openBtn.textContent = 'Open in nous';
    openBtn.addEventListener('click', () => {
      sendBg('promptPanelOpen', { resourceId: state.resourceId, generateSimilar: false });
    });

    actions.append(copyBtn, similarBtn, openBtn);
    wrap.appendChild(actions);

    return wrap;
  }

  function makeChip(text) {
    const chip = document.createElement('span');
    chip.className = 'nous-pp-chip';
    chip.textContent = text;
    return chip;
  }

  async function copyActiveTab(btn) {
    if (!state?.result) return;
    const r = state.result;
    const text =
      state.activeTab === 'json'
        ? r.json
          ? JSON.stringify(r.json, null, 2)
          : ''
        : state.activeTab === 'zh'
          ? r.zh
          : r.en;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      const original = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => {
        btn.textContent = original;
      }, 1200);
    } catch (err) {
      console.error('Copy failed:', err);
    }
  }
}
