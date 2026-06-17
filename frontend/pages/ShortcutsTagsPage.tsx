import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { Tag as TagIcon, FolderOpen, Check, Flame, Plus, X } from 'lucide-react';

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
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved'>('idle');
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newTagInput, setNewTagInput] = useState('');
  const [translatedName, setTranslatedName] = useState('');
  const [newTagGroupId, setNewTagGroupId] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

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

  // Case-insensitive search across English + Chinese names
  const query = search.trim().toLowerCase();
  const filteredTags = useMemo(() => {
    if (!query) return tags;
    return tags.filter((t) => {
      const name = (t.name || '').toLowerCase();
      const nameZh = (t.name_zh || '').toLowerCase();
      return name.includes(query) || nameZh.includes(query);
    });
  }, [tags, query]);

  // Top 8 most used tags — hidden while searching so results stay flat
  const topTags = useMemo(() => {
    if (query) return [];
    return [...tags]
      .filter((t) => t.media_count > 0)
      .sort(byMediaCountDesc)
      .slice(0, 8);
  }, [tags, query]);

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
  }, [filteredTags, query]);

  // Extract unique tag groups for create form dropdown
  const tagGroups = useMemo<TagGroup[]>(() => {
    const seen = new Map<string, string>();
    for (const tag of tags) {
      if (tag.group_id && tag.group_name && !seen.has(tag.group_id)) {
        seen.set(tag.group_id, tag.group_name);
      }
    }
    return Array.from(seen.entries()).map(([id, name]) => ({ id, name }));
  }, [tags]);

  // Auto-translate: detect input language, translate to the other
  const translateTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isChinese = (text: string) => /[\u4e00-\u9fff]/.test(text);

  const handleInputChange = useCallback((value: string) => {
    setNewTagInput(value);
    if (translateTimerRef.current) clearTimeout(translateTimerRef.current);
    if (!value.trim()) {
      setTranslatedName('');
      return;
    }
    translateTimerRef.current = setTimeout(async () => {
      try {
        const langPair = isChinese(value) ? 'zh|en' : 'en|zh';
        const res = await fetch(
          `https://api.mymemory.translated.net/get?q=${encodeURIComponent(value.trim())}&langpair=${langPair}&de=8512939@qq.com`
        );
        if (!res.ok) return;
        const data = await res.json();
        const translated = data?.responseData?.translatedText;
        if (translated && translated !== value) {
          setTranslatedName(translated);
        }
      } catch {
        // Translation is optional
      }
    }, 600);
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
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Failed' }));
        throw new Error(err.detail || `Error ${res.status}`);
      }
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
  }, [newTagInput, translatedName, newTagGroupId, token]);

  /** Get display label (may be Chinese), but always use English name for storage */
  const getStorageName = (tag: Tag) => tag.name;

  // Toggle tag and auto-save to Redis
  const toggle = useCallback((tagName: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(tagName)) {
        next.delete(tagName);
      } else {
        next.add(tagName);
      }
      // Auto-save to Redis
      if (token && next.size > 0) {
        setSaveStatus('saving');
        fetch(`${API_BASE}/api/v1/auth/temp-token/${token}/selection`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tags: Array.from(next) }),
        })
          .then(() => {
            setSaveStatus('saved');
            setTimeout(() => setSaveStatus('idle'), 1500);
          })
          .catch((err) => {
            console.error('Failed to save selection:', err);
            setSaveStatus('idle');
          });
      } else if (token && next.size === 0) {
        // Clear selection
        fetch(`${API_BASE}/api/v1/auth/temp-token/${token}/selection`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tags: [] }),
        }).catch((err) => console.error('Failed to clear selection:', err));
      }
      return next;
    });
  }, [token]);

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
              <span className="text-indigo-400">{translatedName}</span>
            </div>
          )}
          <select
            value={newTagGroupId}
            onChange={(e) => setNewTagGroupId(e.target.value)}
            className="w-full px-3 py-2 rounded-lg bg-ink-800 border border-ink-700 text-sm text-ink-50 outline-none focus:border-indigo-500"
          >
            <option value="">{lang === 'zh' ? '选择分组 (可选)' : 'Select group (optional)'}</option>
            {tagGroups.map((g) => (
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </select>
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
        {filteredTags.length === 0 && (
          <p className="text-center text-sm text-ink-500 py-8">
            {query
              ? (lang === 'zh' ? '没有匹配的标签' : 'No tags match your search')
              : (lang === 'zh' ? '暂无标签' : 'No tags yet')}
          </p>
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
          {selected.size === 0 && (
            <span className="text-ink-500">
              {lang === 'zh' ? '点击标签选择，选完关闭即可' : 'Tap tags to select, close when done'}
            </span>
          )}
          {selected.size > 0 && saveStatus === 'saving' && (
            <span className="text-ink-400">
              {lang === 'zh' ? `保存中... (${selected.size})` : `Saving... (${selected.size})`}
            </span>
          )}
          {selected.size > 0 && saveStatus === 'saved' && (
            <span className="text-emerald-400">
              {lang === 'zh' ? `已保存 ✓ (${selected.size})` : `Saved ✓ (${selected.size})`}
            </span>
          )}
          {selected.size > 0 && saveStatus === 'idle' && (
            <span className="text-ink-400">
              {lang === 'zh' ? `已选 ${selected.size} 个` : `${selected.size} selected`}
            </span>
          )}
        </p>
      </div>
    </div>
  );
};
