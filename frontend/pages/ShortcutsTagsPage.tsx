import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { Tag as TagIcon, FolderOpen, Check, Flame, Plus, X, Star, FileText, BookOpen, ScanEye } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { UiSelect } from '../components/ui';
import { PIPELINE_TAG_GROUP } from '../utils/aiIntents';

interface Tag {
  id: string;
  name: string;
  name_zh: string | null;
  color: string;
  group_id: string | null;
  group_name: string | null;
  media_count: number;
  type: string;
}

interface TagGroup {
  id: string;
  name: string;
}

const API_BASE = import.meta.env.VITE_API_URL || '';

/** Processing options saved together with the tag selection. */
type Options = { rating: number | null; transcribe: boolean; summarize: boolean; analyze: boolean };
const DEFAULT_OPTIONS: Options = { rating: null, transcribe: false, summarize: false, analyze: false };

const RATING_STARS = [1, 2, 3, 4, 5] as const;

type IntentKey = 'transcribe' | 'summarize' | 'analyze';
/** Icon toggles, left to right; icons match the AI status badges on resource cards (CompactMediaCard). */
const AI_INTENTS: ReadonlyArray<{ key: IntentKey; Icon: LucideIcon; zh: string; en: string }> = [
  { key: 'transcribe', Icon: FileText, zh: '转录', en: 'Transcribe' },
  { key: 'summarize', Icon: BookOpen, zh: '总结', en: 'Summary' },
  { key: 'analyze', Icon: ScanEye, zh: '解析', en: 'Analyze' },
];

/** Wait after the last keystroke before asking MyMemory for a counterpart name. */
const TRANSLATE_DEBOUNCE_MS = 600;

/**
 * Abort a selection POST that hasn't settled by then. Saves are serialized, so a
 * stalled request on a flaky mobile link would otherwise hold every queued save
 * (including the final state) until the browser's own network timeout.
 */
const SAVE_TIMEOUT_MS = 12_000;

const isChinese = (text: string) => /[\u4e00-\u9fff]/.test(text);

/** Counterpart name (zh↔en) from MyMemory; '' when unavailable — translation is optional. */
const fetchTranslation = async (text: string): Promise<string> => {
  try {
    const langPair = isChinese(text) ? 'zh|en' : 'en|zh';
    const res = await fetch(
      `https://api.mymemory.translated.net/get?q=${encodeURIComponent(text)}&langpair=${langPair}&de=8512939@qq.com`,
    );
    if (!res.ok) return '';
    const data = await res.json();
    const translated = data?.responseData?.translatedText;
    return translated && translated !== text ? translated : '';
  } catch (err) {
    console.warn('Tag name translation unavailable:', err);
    return '';
  }
};

type CreateConflict = { name?: string; name_zh?: string; type?: string };

/**
 * User-facing message for a failed POST /tags. Production wraps HTTPException in
 * the ErrorResponse envelope ({error, code, details}); bare FastAPI uses
 * {detail}. Both are read so the typed 409 conflict survives either shape.
 */
const describeCreateError = async (res: Response, name: string, lang: string): Promise<string> => {
  const body = await res.json().catch(() => ({}) as Record<string, unknown>);
  const structured = body?.details ?? body?.detail;
  // Only a 4xx with a string detail carries a real message in `error`; dict details
  // become "Request failed" and every 5xx becomes "Internal server error".
  const human = res.status < 500 && body?.details == null ? (body?.error ?? body?.detail) : undefined;
  if (res.status === 409) {
    // Backend names WHICH tag conflicts. The English name (often an
    // auto-translation, e.g. 康复 -> "Healing") may collide with a
    // built-in system tag whose Chinese alias differs (Healing/治愈).
    // The Chinese name isn't the duplicate — surface the real conflict.
    const conflict =
      structured && typeof structured === 'object'
        ? (structured as { conflict?: CreateConflict }).conflict
        : undefined;
    if (conflict?.name) {
      const existingLabel = conflict.name_zh
        ? `${conflict.name_zh}（${conflict.name}）`
        : conflict.name;
      const kind =
        conflict.type === 'system'
          ? lang === 'zh'
            ? '系统内置标签'
            : 'a built-in tag'
          : lang === 'zh'
            ? '已有标签'
            : 'an existing tag';
      return lang === 'zh'
        ? `英文名「${name}」已被${kind}「${existingLabel}」占用。换个英文名，或在上方搜索选择已有标签。`
        : `The English name "${name}" is already used by ${kind} "${existingLabel}". Use a different English name, or search and select the existing tag above.`;
    }
    return lang === 'zh'
      ? `标签已存在：英文名「${name}」已被占用。请换个名字，或在上方搜索选择已有标签。`
      : `Tag already exists: "${name}" is taken. Use a different name, or search and select the existing tag above.`;
  }
  return (
    (typeof human === 'string' && human) ||
    (lang === 'zh' ? `创建失败（${res.status}）` : `Create failed (${res.status})`)
  );
};

/** Most-used first within any list (media_count descending) */
const byMediaCountDesc = (a: Tag, b: Tag) =>
  (b.media_count ?? 0) - (a.media_count ?? 0);

/** Generate tag pill style: gray when unselected, colored when selected */
const getTagStyle = (color: string, isSelected: boolean) => {
  const baseColor = color || '#3b82f6';
  if (isSelected) {
    return {
      backgroundColor: `${baseColor}25`,
      color: baseColor,
      borderColor: baseColor,
      boxShadow: `0 0 12px ${baseColor}40, inset 0 0 12px ${baseColor}15`,
    };
  }
  // Unselected: theme-aware via CSS vars (the ink ladder + content tokens flip
  // per data-theme). The old hardcoded dark grays (rgba(63,63,70,.3)/#a1a1aa)
  // were near-invisible on the light-theme white page.
  return {
    backgroundColor: 'var(--ink-800)',
    color: 'var(--content-2)',
    borderColor: 'var(--ink-700)',
  };
};

export const ShortcutsTagsPage: React.FC = () => {
  const [tags, setTags] = useState<Tag[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newTagInput, setNewTagInput] = useState('');
  const [translatedName, setTranslatedName] = useState('');
  const [newTagGroupId, setNewTagGroupId] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [options, setOptions] = useState<Options>(DEFAULT_OPTIONS);

  // Read params from both query string and hash fragment
  const params = new URLSearchParams(window.location.search);
  const hashParams = new URLSearchParams(window.location.hash.replace('#', ''));

  // Temp token (secure) — preferred over api_key
  const token = params.get('token') || hashParams.get('token') || '';
  // Legacy: api_key via hash fragment (still supported for backward compat)
  const apiKey = hashParams.get('api_key') || params.get('api_key') || '';
  const callbackName = params.get('callback') || hashParams.get('callback') || '';
  const lang = params.get('lang') || hashParams.get('lang') || 'zh';

  /** Get display label based on lang */
  const getLabel = (tag: Tag) =>
    lang === 'zh' ? (tag.name_zh || tag.name) : tag.name;

  useEffect(() => {
    if (!token && !apiKey) {
      setError('Missing token parameter');
      setLoading(false);
      return;
    }

    // Determine fetch URL based on auth method
    const fetchUrl = token
      ? `${API_BASE}/api/v1/auth/temp-token/${token}/tags?enabled_only=true`
      : `${API_BASE}/api/v1/tags?enabled_only=true`;

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (!token && apiKey) {
      headers['X-API-Key'] = apiKey;
    }

    fetch(fetchUrl, { headers })
      .then((res) => {
        if (!res.ok) {
          if (res.status === 401) throw new Error('Token expired or invalid');
          throw new Error(`API error: ${res.status}`);
        }
        return res.json();
      })
      .then((data) => {
        setTags(data.tags || []);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [token, apiKey]);

  // Pipeline 组（Transcript / Summary / Analyze）由下方四项单选承载，不再当标签选。
  const pickableTags = useMemo(
    () => tags.filter((t) => t.group_name !== PIPELINE_TAG_GROUP),
    [tags],
  );

  // Case-insensitive search across English + Chinese names
  const query = search.trim().toLowerCase();
  const filteredTags = useMemo(() => {
    if (!query) return pickableTags;
    return pickableTags.filter((t) => {
      const name = (t.name || '').toLowerCase();
      const nameZh = (t.name_zh || '').toLowerCase();
      return name.includes(query) || nameZh.includes(query);
    });
  }, [pickableTags, query]);

  // Top 8 most used tags — hidden while searching so results stay flat
  const topTags = useMemo(() => {
    if (query) return [];
    return [...pickableTags]
      .filter((t) => t.media_count > 0)
      .sort(byMediaCountDesc)
      .slice(0, 8);
  }, [pickableTags, query]);

  // Group tags by group_name, exclude ungrouped tags from showing as "Other"
  const grouped = useMemo(() => {
    const map = new Map<string, Tag[]>();
    const ungrouped: Tag[] = [];
    for (const tag of filteredTags) {
      if (!tag.group_name) {
        ungrouped.push(tag);
      } else {
        if (!map.has(tag.group_name)) map.set(tag.group_name, []);
        map.get(tag.group_name)!.push(tag);
      }
    }
    const entries = Array.from(map.entries());
    // Ungrouped tags always render as their own "未分类" section — never
    // tail-appended to the last group (would visually bleed into it; same
    // bug the chrome-extension v1.1.4 had — fixed in v1.1.5 by pushing
    // a dedicated entry instead).
    if (ungrouped.length > 0) {
      entries.push(['未分类', ungrouped]);
    }
    // Sort each group most-used first (immutable copy, no in-place mutation)
    return entries.map(
      ([name, list]) =>
        [name, [...list].sort(byMediaCountDesc)] as [string, Tag[]],
    );
  }, [filteredTags]);

  // Extract unique tag groups for create form dropdown (Pipeline is not pickable)
  const tagGroups = useMemo<TagGroup[]>(() => {
    const seen = new Map<string, string>();
    for (const tag of pickableTags) {
      if (tag.group_id && tag.group_name && !seen.has(tag.group_id)) {
        seen.set(tag.group_id, tag.group_name);
      }
    }
    return Array.from(seen.entries()).map(([id, name]) => ({ id, name }));
  }, [pickableTags]);

  // Auto-translate: detect input language, translate to the other
  const translateTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (translateTimerRef.current) clearTimeout(translateTimerRef.current);
  }, []);

  const handleInputChange = useCallback((value: string) => {
    setNewTagInput(value);
    if (translateTimerRef.current) clearTimeout(translateTimerRef.current);
    if (!value.trim()) {
      setTranslatedName('');
      return;
    }
    translateTimerRef.current = setTimeout(async () => {
      const translated = await fetchTranslation(value.trim());
      if (translated) setTranslatedName(translated);
    }, TRANSLATE_DEBOUNCE_MS);
  }, []);

  const handleCreateTag = useCallback(async () => {
    const input = newTagInput.trim();
    if (!input || !token) return;

    // Determine which is name (en) and which is name_zh based on input language
    const inputIsChinese = isChinese(input);
    const name = inputIsChinese ? (translatedName || input) : input;
    const name_zh = inputIsChinese ? input : (translatedName || null);

    setCreating(true);
    setCreateError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/auth/temp-token/${token}/tags`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          name_zh,
          group_id: newTagGroupId || null,
        }),
      });
      if (!res.ok) throw new Error(await describeCreateError(res, name, lang));
      // Reload tags
      const fetchUrl = `${API_BASE}/api/v1/auth/temp-token/${token}/tags?enabled_only=true`;
      const tagsRes = await fetch(fetchUrl);
      if (tagsRes.ok) {
        const data = await tagsRes.json();
        setTags(data.tags || []);
      }
      setNewTagInput('');
      setTranslatedName('');
      setNewTagGroupId('');
      setShowCreateForm(false);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create tag');
    } finally {
      setCreating(false);
    }
  }, [newTagInput, translatedName, newTagGroupId, token, lang]);

  /** Get display label (may be Chinese), but always use English name for storage */
  const getStorageName = (tag: Tag) => tag.name;

  // Only the most recent save may drive the status line: an older response (or
  // its idle timer) landing late must not overwrite the newer outcome.
  const saveSeqRef = useRef(0);
  // Saves run one at a time: the backend replaces the whole selection per POST,
  // so two in flight could land out of order and leave an older state stored.
  // Each link catches its own failure, so the chain itself never rejects.
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());

  // 单次 POST 全字段：标签 + 评级 + 三项 AI 开关。后端每次整体替换，所以五个键必须都发；空标签也照发。
  const saveSelection = useCallback((nextTags: Set<string>, nextOptions: Options) => {
    if (!token) return;
    const seq = ++saveSeqRef.current;
    const isLatest = () => seq === saveSeqRef.current;
    // Body is captured now — the state at the moment of the user's action.
    const body = JSON.stringify({ tags: Array.from(nextTags), ...nextOptions });
    setSaveStatus('saving');
    saveChainRef.current = saveChainRef.current.then(() => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), SAVE_TIMEOUT_MS);
      return fetch(`${API_BASE}/api/v1/auth/temp-token/${token}/selection`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        signal: controller.signal,
      })
        .then((res) => {
          if (!res.ok) throw new Error(`Save selection failed: HTTP ${res.status}`);
          if (!isLatest()) return;
          setSaveStatus('saved');
          setTimeout(() => {
            if (isLatest()) setSaveStatus('idle');
          }, 1500);
        })
        .catch((err) => {
          console.error('Failed to save selection:', err);
          if (isLatest()) setSaveStatus('error');
        })
        .finally(() => clearTimeout(timeout));
    });
  }, [token]);

  // Next state is computed from the current render's values, never inside a
  // state updater: StrictMode double-invokes updaters, which would double-POST.
  const toggle = useCallback((tagName: string) => {
    const next = new Set(selected);
    if (next.has(tagName)) {
      next.delete(tagName);
    } else {
      next.add(tagName);
    }
    setSelected(next);
    // 从搜索结果里点中即清空搜索，列表回到全量视图。
    setSearch('');
    saveSelection(next, options);
  }, [selected, options, saveSelection]);

  const setOption = useCallback(<K extends keyof Options>(key: K, value: Options[K]) => {
    const next: Options = { ...options, [key]: value };
    setOptions(next);
    saveSelection(selected, next);
  }, [selected, options, saveSelection]);

  // quickCreate awaits the network before selecting; by then the user may have
  // changed options, so it must select through the latest toggle, not the one
  // captured when the create button was clicked.
  const toggleRef = useRef(toggle);
  useEffect(() => {
    toggleRef.current = toggle;
  }, [toggle]);

  // Search-or-create bar (mirrors chrome-extension popup.js syncQuickCreateBar).
  const [quickTranslate, setQuickTranslate] = useState('');
  const quickTouchedRef = useRef(false);
  const [quickCreating, setQuickCreating] = useState(false);
  const [quickError, setQuickError] = useState<string | null>(null);
  const quickTerm = search.trim();
  const showQuickCreate = !!token && !!query && filteredTags.length === 0 && !showCreateForm;

  useEffect(() => {
    // New query → drop the previous suggestion and ask for a fresh one; an
    // edited / "="-ed value is the user's and survives.
    quickTouchedRef.current = false;
    setQuickTranslate('');
    setQuickError(null);
    if (!showQuickCreate) return undefined;
    let cancelled = false;
    const timer = setTimeout(async () => {
      const translated = await fetchTranslation(quickTerm);
      if (!cancelled && translated && !quickTouchedRef.current) setQuickTranslate(translated);
    }, TRANSLATE_DEBOUNCE_MS);
    return () => {
      cancelled = true; // a translation already in flight must not land on a newer query
      clearTimeout(timer);
    };
  }, [quickTerm, showQuickCreate]);

  const quickCreate = async () => {
    const term = quickTerm;
    if (!term || !token || quickCreating) return;
    const other = quickTranslate.trim();
    const payload = isChinese(term)
      ? { name: other || term, name_zh: term, group_id: null }
      : { name: term, name_zh: other || null, group_id: null };
    setQuickCreating(true);
    setQuickError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/auth/temp-token/${token}/tags`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await describeCreateError(res, payload.name, lang));
      const tagsRes = await fetch(`${API_BASE}/api/v1/auth/temp-token/${token}/tags?enabled_only=true`);
      if (tagsRes.ok) {
        const data = await tagsRes.json();
        setTags(data.tags || []);
      } else {
        console.error('Failed to reload tags after create:', tagsRes.status);
      }
      toggleRef.current(payload.name); // selects + clears the search
    } catch (err) {
      setQuickError(err instanceof Error ? err.message : 'Failed to create tag');
    } finally {
      setQuickCreating(false);
    }
  };

  const ratingLabel = lang === 'zh' ? '评级' : 'Rating';

  const countSuffix = selected.size > 0 ? ` (${selected.size})` : '';

  if (loading) {
    return (
      <div className="min-h-screen bg-ink-950 flex items-center justify-center">
        <div className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-ink-950 flex items-center justify-center p-6">
        <div className="text-center">
          <p className="text-red-400 text-lg mb-2">Error</p>
          <p className="text-ink-400 text-sm">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-ink-950 text-ink-50 pb-28">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-ink-950/95 backdrop-blur-sm border-b border-ink-800 px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="w-8" />
          <h1 className="text-lg font-semibold text-center">
            {lang === 'zh' ? '选择标签' : 'Select Tags'}
          </h1>
          <button
            onClick={() => setShowCreateForm(!showCreateForm)}
            aria-label={showCreateForm
              ? (lang === 'zh' ? '关闭新建' : 'Close create form')
              : (lang === 'zh' ? '新建标签' : 'Create tag')}
            className="w-8 h-8 flex items-center justify-center rounded-full bg-ink-800 text-ink-400 hover:text-ink-50 transition-colors"
          >
            {showCreateForm ? <X size={16} /> : <Plus size={16} />}
          </button>
        </div>
        {selected.size > 0 && !showCreateForm && (
          <p className="text-xs text-ink-400 text-center mt-1">
            {lang === 'zh' ? `已选 ${selected.size} 个` : `${selected.size} selected`}
          </p>
        )}
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={lang === 'zh' ? '搜索标签...' : 'Search tags...'}
          className="mt-2 w-full px-3 py-1.5 rounded-lg bg-ink-800 border border-ink-700 text-sm text-ink-50 placeholder-ink-500 outline-none focus:border-indigo-500"
        />
      </div>

      {/* Processing options — replace picking Transcript/Summary/Analyze as tags */}
      <div className="px-4 py-3 border-b border-ink-800 space-y-3">
        {/* Rating: one row that never wraps (label 48px + 5×36px stars fits a 390px screen). */}
        <div role="group" aria-label={ratingLabel} className="flex flex-nowrap items-center gap-2">
          <span className="w-12 shrink-0 text-xs text-ink-400">{ratingLabel}</span>
          <div className="flex flex-nowrap items-center gap-1">
            {RATING_STARS.map((n) => {
              const lit = options.rating !== null && n <= options.rating;
              return (
                <button
                  key={n}
                  type="button"
                  aria-pressed={lit}
                  aria-label={lang === 'zh' ? `${n} 星` : `${n} ${n === 1 ? 'star' : 'stars'}`}
                  data-testid={`opt-rating-${n}`}
                  // Tapping the current rating again clears it — there is no separate "none" chip.
                  onClick={() => setOption('rating', options.rating === n ? null : n)}
                  className="w-9 h-9 shrink-0 flex items-center justify-center rounded-full transition-colors active:bg-ink-800"
                >
                  <Star
                    size={22}
                    aria-hidden="true"
                    className={lit ? 'fill-current text-[var(--accent-text)]' : 'text-ink-500'}
                  />
                </button>
              );
            })}
          </div>
        </div>
        {/* AI intents: three icon toggles in one row — lit means on. */}
        <div role="group" aria-label={lang === 'zh' ? 'AI 处理' : 'AI processing'} className="grid grid-cols-3 gap-2">
          {AI_INTENTS.map(({ key, Icon, zh, en }) => {
            const on = options[key];
            return (
              <button
                key={key}
                type="button"
                aria-pressed={on}
                data-testid={`opt-${key}`}
                onClick={() => setOption(key, !on)}
                className={`h-10 min-w-0 flex items-center justify-center gap-1.5 px-2 rounded-lg border text-xs transition-colors ${
                  on
                    ? 'bg-[var(--accent-soft)] text-[var(--accent-text)] border-[var(--accent-border)]'
                    : 'bg-ink-800 text-ink-400 border-ink-700'
                }`}
              >
                <Icon size={16} aria-hidden="true" className="shrink-0" />
                <span className="truncate">{lang === 'zh' ? zh : en}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Create tag form */}
      {showCreateForm && (
        <div className="px-4 py-3 border-b border-ink-800 bg-ink-900/50 space-y-3">
          <input
            type="text"
            value={newTagInput}
            onChange={(e) => handleInputChange(e.target.value)}
            placeholder={lang === 'zh' ? '输入标签名（中文或英文）' : 'Enter tag name (Chinese or English)'}
            className="w-full px-3 py-2 rounded-lg bg-ink-800 border border-ink-700 text-sm text-ink-50 placeholder-ink-500 outline-none focus:border-indigo-500"
            autoFocus
          />
          {translatedName && (
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-ink-800/50 text-xs">
              <span className="text-ink-500">{isChinese(newTagInput) ? 'EN:' : 'ZH:'}</span>
              <span className="text-[var(--accent-text)]">{translatedName}</span>
            </div>
          )}
          <UiSelect
            value={newTagGroupId}
            onChange={(e) => setNewTagGroupId(e.target.value)}
            className="w-full"
          >
            <option value="">{lang === 'zh' ? '选择分组 (可选)' : 'Select group (optional)'}</option>
            {tagGroups.map((g) => (
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </UiSelect>
          {createError && (
            <p className="text-xs text-red-400">{createError}</p>
          )}
          <button
            onClick={handleCreateTag}
            disabled={!newTagInput.trim() || creating}
            className="w-full py-2 rounded-lg bg-indigo-600 text-sm font-medium text-white disabled:opacity-40 transition-colors hover:bg-indigo-500 active:scale-[0.98]"
          >
            {creating
              ? (lang === 'zh' ? '创建中...' : 'Creating...')
              : (lang === 'zh' ? '创建标签' : 'Create Tag')}
          </button>
        </div>
      )}

      {/* Tag groups */}
      <div className="px-4 py-3 space-y-5">
        {filteredTags.length === 0 && !showQuickCreate && (
          <p className="text-center text-sm text-ink-500 py-8">
            {query
              ? (lang === 'zh' ? '没有匹配的标签' : 'No tags match your search')
              : (lang === 'zh' ? '暂无标签' : 'No tags yet')}
          </p>
        )}
        {showQuickCreate && (
          <div className="rounded-lg border border-ink-700 bg-ink-900/50 p-3 space-y-2">
            <p className="text-xs text-ink-500">
              {lang === 'zh' ? '没有匹配的标签' : 'No tags match your search'}
            </p>
            <div className="flex items-center gap-2 text-xs">
              <span className="text-ink-500 w-8">{isChinese(quickTerm) ? 'EN:' : 'ZH:'}</span>
              <input
                data-testid="quick-translate-input"
                value={quickTranslate}
                onChange={(e) => { quickTouchedRef.current = true; setQuickTranslate(e.target.value); }}
                placeholder={lang === 'zh' ? '对应译名（可改）' : 'Counterpart name (editable)'}
                className="flex-1 min-w-0 px-2 py-1 rounded bg-ink-800 border border-ink-700 text-ink-50 placeholder-ink-500 outline-none focus:border-[var(--accent-border)]"
              />
              <button
                type="button"
                data-testid="quick-same-btn"
                title={lang === 'zh' ? '两种语言同名' : 'Same in both languages'}
                aria-label={lang === 'zh' ? '两种语言同名' : 'Same in both languages'}
                onClick={() => { quickTouchedRef.current = true; setQuickTranslate(quickTerm); }}
                className="px-2 py-1 rounded border border-ink-700 text-ink-300"
              >
                =
              </button>
            </div>
            {quickError && <p className="text-xs text-danger">{quickError}</p>}
            <button
              type="button"
              data-testid="quick-create-btn"
              onClick={quickCreate}
              disabled={quickCreating}
              className="w-full py-2 rounded-lg border bg-[var(--accent-soft)] text-[var(--accent-text)] border-[var(--accent-border)] text-sm font-medium disabled:opacity-40 active:scale-[0.98]"
            >
              {quickCreating
                ? (lang === 'zh' ? `创建「${quickTerm}」中...` : `Creating "${quickTerm}"...`)
                : (lang === 'zh' ? `创建「${quickTerm}」` : `Create "${quickTerm}"`)}
            </button>
          </div>
        )}
        {/* Frequently used */}
        {topTags.length > 0 && (
          <div>
            <div className="flex items-center gap-2 mb-2.5">
              <Flame size={14} className="text-orange-500" />
              <span className="text-xs font-semibold text-orange-400 uppercase tracking-wider">
                {lang === 'zh' ? '常用' : 'Frequently Used'}
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              {topTags.map((tag) => {
                const label = getLabel(tag);
                const name = getStorageName(tag);
                const isSelected = selected.has(name);
                const style = getTagStyle(tag.color, isSelected);
                return (
                  <button
                    key={`top-${tag.id}`}
                    onClick={() => toggle(name)}
                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-full border text-xs font-medium transition-all active:scale-95"
                    style={style}
                  >
                    {isSelected ? (
                      <Check size={11} className="shrink-0" />
                    ) : (
                      <TagIcon size={11} className="shrink-0" />
                    )}
                    <span className="truncate">{label}</span>
                    <span className="opacity-50 shrink-0 text-[10px]">
                      {tag.media_count}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {grouped.map(([groupName, groupTags]) => (
          <div key={groupName}>
            {/* Group header matching TagsSettings style */}
            <div className="flex items-center gap-2 mb-2.5">
              <FolderOpen size={14} className="text-ink-500" />
              <span className="text-xs font-semibold text-ink-400 uppercase tracking-wider">
                {groupName}
              </span>
              <span className="text-[10px] text-ink-600">({groupTags.length})</span>
            </div>

            {/* Tags — flex wrap for compact layout */}
            <div className="flex flex-wrap gap-2">
              {groupTags.map((tag) => {
                const label = getLabel(tag);
                const name = getStorageName(tag);
                const isSelected = selected.has(name);
                const style = getTagStyle(tag.color, isSelected);

                return (
                  <button
                    key={tag.id}
                    onClick={() => toggle(name)}
                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-full border text-xs font-medium transition-all active:scale-95 truncate"
                    style={style}
                  >
                    {isSelected ? (
                      <Check size={11} className="shrink-0" />
                    ) : (
                      <TagIcon size={11} className="shrink-0" />
                    )}
                    <span className="truncate">{label}</span>
                    <span className="opacity-50 shrink-0 text-[10px]">
                      {tag.media_count ?? 0}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* Fixed bottom bar — status only, auto-saved on every toggle */}
      <div className="fixed bottom-0 left-0 right-0 p-3 bg-ink-950/95 backdrop-blur-sm border-t border-ink-800 safe-area-pb">
        <p className="text-center text-sm">
          {saveStatus === 'error' && (
            <span className="text-danger">
              {lang === 'zh' ? '保存失败，请重试任一选项' : 'Save failed — change any option to retry'}
            </span>
          )}
          {saveStatus === 'saving' && (
            <span className="text-ink-400">
              {lang === 'zh' ? '保存中...' : 'Saving...'}{countSuffix}
            </span>
          )}
          {saveStatus === 'saved' && (
            <span className="text-ok">
              {lang === 'zh' ? '已保存 ✓' : 'Saved ✓'}{countSuffix}
            </span>
          )}
          {saveStatus === 'idle' && selected.size === 0 && (
            <span className="text-ink-500">
              {lang === 'zh' ? '选择标签或选项，选完关闭即可' : 'Pick tags or options, close when done'}
            </span>
          )}
          {saveStatus === 'idle' && selected.size > 0 && (
            <span className="text-ink-400">
              {lang === 'zh' ? `已选 ${selected.size} 个` : `${selected.size} selected`}
            </span>
          )}
        </p>
      </div>
    </div>
  );
};
