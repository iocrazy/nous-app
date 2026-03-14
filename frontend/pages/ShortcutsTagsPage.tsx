import { useState, useEffect, useMemo, useCallback } from 'react';
import { Tag as TagIcon, FolderOpen, Check, Flame } from 'lucide-react';

interface Tag {
  id: string;
  name: string;
  name_zh: string | null;
  color: string;
  group_name: string | null;
  media_count: number;
  type: string;
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

  const toggle = useCallback((tagName: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(tagName)) {
        next.delete(tagName);
      } else {
        next.add(tagName);
      }
      return next;
    });
  }, []);

  const [confirmed, setConfirmed] = useState(false);

  const handleConfirm = async () => {
    const selectedList = Array.from(selected).join(',');
    try {
      await navigator.clipboard.writeText(selectedList);
      setConfirmed(true);
    } catch {
      // Fallback for older browsers
      const textarea = document.createElement('textarea');
      textarea.value = selectedList;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      setConfirmed(true);
    }
  };

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
        <h1 className="text-lg font-semibold text-center">
          {lang === 'zh' ? '选择标签' : 'Select Tags'}
        </h1>
        {selected.size > 0 && (
          <p className="text-xs text-zinc-400 text-center mt-1">
            {lang === 'zh' ? `已选 ${selected.size} 个` : `${selected.size} selected`}
          </p>
        )}
      </div>

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
                const isSelected = selected.has(label);
                const style = getTagStyle(tag.color, isSelected);
                return (
                  <button
                    key={`top-${tag.id}`}
                    onClick={() => toggle(label)}
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
                const isSelected = selected.has(label);
                const style = getTagStyle(tag.color, isSelected);

                return (
                  <button
                    key={tag.id}
                    onClick={() => toggle(label)}
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

      {/* Fixed bottom confirm button */}
      <div className="fixed bottom-0 left-0 right-0 p-4 bg-zinc-950/95 backdrop-blur-sm border-t border-zinc-800 safe-area-pb">
        {confirmed ? (
          <div className="text-center py-3">
            <p className="text-green-400 text-base font-semibold">
              {lang === 'zh' ? '已复制到剪贴板，请点 Done 关闭' : 'Copied! Tap Done to close'}
            </p>
          </div>
        ) : (
          <button
            onClick={handleConfirm}
            disabled={selected.size === 0}
            className={`w-full py-3 rounded-xl text-base font-semibold transition-all ${
              selected.size > 0
                ? 'bg-indigo-600 text-white active:bg-indigo-700'
                : 'bg-zinc-800 text-zinc-500 cursor-not-allowed'
            }`}
          >
            {selected.size > 0
              ? (lang === 'zh' ? `确认 (${selected.size})` : `Confirm (${selected.size})`)
              : (lang === 'zh' ? '请选择标签' : 'Select tags to continue')}
          </button>
        )}
      </div>
    </div>
  );
};
