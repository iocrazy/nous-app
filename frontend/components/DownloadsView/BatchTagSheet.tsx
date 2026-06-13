// frontend/components/DownloadsView/BatchTagSheet.tsx
//
// Bottom sheet to apply tags to a multi-selection (Downloads batch "Tag"
// action). Pick one or more tags, then Apply — the parent resolves each
// selected item to its resource and calls the tag API. Searchable + grouped so
// it scales with the tag catalog (same pattern as the filter tag picker).

import { useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, Check, Search } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { Tag } from '../../types';

interface BatchTagSheetProps {
  open: boolean;
  count: number;
  allTags: Tag[];
  onApply: (tagIds: string[]) => Promise<void>;
  onClose: () => void;
}

export function BatchTagSheet({
  open,
  count,
  allTags,
  onApply,
  onClose,
}: BatchTagSheetProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [applying, setApplying] = useState(false);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return allTags
      .filter(
        (tag) =>
          !q ||
          tag.name.toLowerCase().includes(q) ||
          (tag.name_zh ?? '').toLowerCase().includes(q),
      )
      .sort((a, b) => {
        const pa = picked.has(a.id) ? 1 : 0;
        const pb = picked.has(b.id) ? 1 : 0;
        if (pa !== pb) return pb - pa;
        return a.name.localeCompare(b.name);
      });
  }, [allTags, query, picked]);

  if (!open) return null;

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const apply = async () => {
    if (picked.size === 0 || applying) return;
    setApplying(true);
    try {
      await onApply(Array.from(picked));
      setPicked(new Set());
      setQuery('');
      onClose();
    } catch (err) {
      console.error('[BatchTagSheet] apply failed:', err);
    } finally {
      setApplying(false);
    }
  };

  return createPortal(
    <div className="md:hidden fixed inset-0 z-[75] flex flex-col bg-ink-950">
      <div
        className="px-4 py-3 flex items-center justify-between border-b border-ink-800 shrink-0"
        style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 12px)' }}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label={t('common.close', 'Close')}
          className="w-9 h-9 rounded-full bg-ink-800 flex items-center justify-center text-ink-300 active:bg-ink-700"
        >
          <X size={18} />
        </button>
        <span className="text-sm font-semibold text-ink-50">
          {t('resources.batchAddTags', 'Add tags to {{count}}', { count })}
        </span>
        <button
          type="button"
          onClick={apply}
          disabled={picked.size === 0 || applying}
          className="px-3 h-9 rounded-full bg-indigo-500 text-white text-sm font-semibold active:bg-indigo-600 disabled:opacity-40"
        >
          {t('common.apply', 'Apply')}
          {picked.size > 0 ? ` (${picked.size})` : ''}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {rows.length === 0 ? (
          <div className="px-4 py-6 text-sm text-ink-500">
            {t('resources.filter.noTags', 'No matching tags')}
          </div>
        ) : (
          rows.map((tag) => {
            const on = picked.has(tag.id);
            return (
              <button
                key={tag.id}
                type="button"
                onClick={() => toggle(tag.id)}
                className={`w-full px-4 py-3 flex items-center gap-3 text-left border-b border-ink-800/50 ${
                  on ? 'bg-indigo-500/10' : 'active:bg-ink-800/50'
                }`}
              >
                <span
                  className={`w-5 h-5 rounded-md flex items-center justify-center shrink-0 ${
                    on ? 'bg-indigo-500 text-white' : 'border border-ink-600'
                  }`}
                >
                  {on && <Check size={13} />}
                </span>
                <span className="text-sm text-ink-50 flex-1 truncate">
                  {tag.name}
                </span>
              </button>
            );
          })
        )}
      </div>

      <div
        className="px-4 py-3 border-t border-ink-800 shrink-0"
        style={{ paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 12px)' }}
      >
        <div className="flex items-center gap-2 bg-ink-800 border border-ink-700 rounded-full px-4 py-2.5">
          <Search size={16} className="text-ink-500 shrink-0" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('resources.filter.searchTags', 'Search tags…')}
            className="flex-1 min-w-0 bg-transparent outline-none text-sm text-ink-50 placeholder-ink-500"
          />
        </div>
      </div>
    </div>,
    document.body,
  );
}
