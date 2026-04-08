import { useState, useEffect, useMemo, useCallback } from 'react';
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
  return {
    backgroundColor: 'rgba(63, 63, 70, 0.3)',
    color: '#a1a1aa',
    borderColor: '#3f3f46',
  };
};

export const ShortcutsTagsPage: React.FC = () => {
  const [tags, setTags] = useState<Tag[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved'>('idle');
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newTagName, setNewTagName] = useState('');
  const [newTagNameZh, setNewTagNameZh] = useState('');
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

  // Top 8 most used tags
  const topTags = useMemo(() =>
    [...tags]
      .filter((t) => t.media_count > 0)
      .sort((a, b) => b.media_count - a.media_count)
      .slice(0, 8),
    [tags],
  );

  // Group tags by group_name, exclude ungrouped tags from showing as "Other"
  const grouped = useMemo(() => {
    const map = new Map<string, Tag[]>();
    const ungrouped: Tag[] = [];
    for (const tag of tags) {
      if (!tag.group_name) {
        ungrouped.push(tag);
      } else {
        if (!map.has(tag.group_name)) map.set(tag.group_name, []);
        map.get(tag.group_name)!.push(tag);
      }
    }
    const entries = Array.from(map.entries());
    // Append ungrouped tags to the last group if any, otherwise skip
    if (ungrouped.length > 0 && entries.length > 0) {
      entries[entries.length - 1][1].push(...ungrouped);
    }
    return entries;
  }, [tags]);

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

  const handleCreateTag = useCallback(async () => {
    if (!newTagName.trim() || !token) return;
    setCreating(true);
    setCreateError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/auth/temp-token/${token}/tags`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newTagName.trim(),
          name_zh: newTagNameZh.trim() || null,
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
      setNewTagName('');
      setNewTagNameZh('');
      setNewTagGroupId('');
      setShowCreateForm(false);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create tag');
    } finally {
      setCreating(false);
    }
  }, [newTagName, newTagNameZh, newTagGroupId, token]);

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
      <div className="min-h-screen bg-zinc-950 flex items-center justify-center">
        <div className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-zinc-950 flex items-center justify-center p-6">
        <div className="text-center">
          <p className="text-red-400 text-lg mb-2">Error</p>
          <p className="text-zinc-400 text-sm">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-white pb-28">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-zinc-950/95 backdrop-blur-sm border-b border-zinc-800 px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="w-8" />
          <h1 className="text-lg font-semibold text-center">
            {lang === 'zh' ? '选择标签' : 'Select Tags'}
          </h1>
          <button
            onClick={() => setShowCreateForm(!showCreateForm)}
            className="w-8 h-8 flex items-center justify-center rounded-full bg-zinc-800 text-zinc-400 hover:text-white transition-colors"
          >
            {showCreateForm ? <X size={16} /> : <Plus size={16} />}
          </button>
        </div>
        {selected.size > 0 && !showCreateForm && (
          <p className="text-xs text-zinc-400 text-center mt-1">
            {lang === 'zh' ? `已选 ${selected.size} 个` : `${selected.size} selected`}
          </p>
        )}
      </div>

      {/* Create tag form */}
      {showCreateForm && (
        <div className="px-4 py-3 border-b border-zinc-800 bg-zinc-900/50 space-y-3">
          <input
            type="text"
            value={newTagName}
            onChange={(e) => setNewTagName(e.target.value)}
            placeholder={lang === 'zh' ? '标签名称 (英文)' : 'Tag name (English)'}
            className="w-full px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-sm text-white placeholder-zinc-500 outline-none focus:border-indigo-500"
            autoFocus
          />
          <input
            type="text"
            value={newTagNameZh}
            onChange={(e) => setNewTagNameZh(e.target.value)}
            placeholder={lang === 'zh' ? '中文名称 (可选)' : 'Chinese name (optional)'}
            className="w-full px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-sm text-white placeholder-zinc-500 outline-none focus:border-indigo-500"
          />
          <select
            value={newTagGroupId}
            onChange={(e) => setNewTagGroupId(e.target.value)}
            className="w-full px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-sm text-white outline-none focus:border-indigo-500"
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
            disabled={!newTagName.trim() || creating}
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
              <FolderOpen size={14} className="text-zinc-500" />
              <span className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                {groupName}
              </span>
              <span className="text-[10px] text-zinc-600">({groupTags.length})</span>
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
      <div className="fixed bottom-0 left-0 right-0 p-3 bg-zinc-950/95 backdrop-blur-sm border-t border-zinc-800 safe-area-pb">
        <p className="text-center text-sm">
          {selected.size === 0 && (
            <span className="text-zinc-500">
              {lang === 'zh' ? '点击标签选择，选完关闭即可' : 'Tap tags to select, close when done'}
            </span>
          )}
          {selected.size > 0 && saveStatus === 'saving' && (
            <span className="text-zinc-400">
              {lang === 'zh' ? `保存中... (${selected.size})` : `Saving... (${selected.size})`}
            </span>
          )}
          {selected.size > 0 && saveStatus === 'saved' && (
            <span className="text-emerald-400">
              {lang === 'zh' ? `已保存 ✓ (${selected.size})` : `Saved ✓ (${selected.size})`}
            </span>
          )}
          {selected.size > 0 && saveStatus === 'idle' && (
            <span className="text-zinc-400">
              {lang === 'zh' ? `已选 ${selected.size} 个` : `${selected.size} selected`}
            </span>
          )}
        </p>
      </div>
    </div>
  );
};
