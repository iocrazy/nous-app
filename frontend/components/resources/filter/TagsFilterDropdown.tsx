// frontend/components/resources/filter/TagsFilterDropdown.tsx
//
// Multi-select tag tree grouped by scope (system / user / time).
// Selection uses AND semantics at the filter level — selecting two
// tags matches resources carrying BOTH.

import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Check } from 'lucide-react';

import type { Tag } from '../../../types';

export interface TagsFilterDropdownProps {
  allTags: Tag[];
  selectedTagIds: string[];
  onChange: (nextTagIds: string[]) => void;
  onClearAll: () => void;
}

type TagScope = 'system' | 'user' | 'time';

const SCOPE_ORDER: TagScope[] = ['system', 'user', 'time'];

export const TagsFilterDropdown: React.FC<TagsFilterDropdownProps> = ({
  allTags,
  selectedTagIds,
  onChange,
  onClearAll,
}) => {
  const { t } = useTranslation();

  const grouped = useMemo(() => {
    const buckets: Record<TagScope, Tag[]> = { system: [], user: [], time: [] };
    for (const tag of allTags) {
      const scope: TagScope = tag.type === 'system' || tag.type === 'time' ? tag.type : 'user';
      buckets[scope].push(tag);
    }
    for (const scope of SCOPE_ORDER) {
      buckets[scope].sort((a, b) => a.name.localeCompare(b.name));
    }
    return buckets;
  }, [allTags]);

  const selectedSet = useMemo(() => new Set(selectedTagIds), [selectedTagIds]);

  const toggleTag = (tagId: string) => {
    if (selectedSet.has(tagId)) {
      onChange(selectedTagIds.filter((id) => id !== tagId));
    } else {
      onChange([...selectedTagIds, tagId]);
    }
  };

  const scopeLabels: Record<TagScope, string> = {
    system: t('resources.filter.tagScope.system', 'System'),
    user: t('resources.filter.tagScope.user', 'User'),
    time: t('resources.filter.tagScope.time', 'Time'),
  };

  const hasAny = allTags.length > 0;

  return (
    <div className="w-64 max-h-80 overflow-y-auto py-1" role="menu" aria-label="Tags filter">
      {!hasAny && (
        <div className="px-3 py-4 text-xs text-zinc-500">
          {t('resources.filter.noTags', 'No tags yet')}
        </div>
      )}
      {hasAny &&
        SCOPE_ORDER.map((scope) => {
          const tags = grouped[scope];
          if (tags.length === 0) return null;
          return (
            <div key={scope}>
              <div className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-widest text-zinc-500">
                {scopeLabels[scope]}
              </div>
              {tags.map((tag) => {
                const checked = selectedSet.has(tag.id);
                return (
                  <button
                    key={tag.id}
                    type="button"
                    onClick={() => toggleTag(tag.id)}
                    className={`w-full text-left px-3 py-1.5 text-xs flex items-center justify-between transition-colors ${
                      checked
                        ? 'bg-indigo-500/10 text-indigo-300'
                        : 'text-zinc-300 hover:bg-zinc-800'
                    }`}
                  >
                    <span className="flex items-center gap-2 truncate">
                      {tag.color && (
                        <span
                          className="inline-block w-2 h-2 rounded-full shrink-0"
                          style={{ backgroundColor: tag.color }}
                        />
                      )}
                      <span className="truncate">{tag.name}</span>
                    </span>
                    {checked && <Check size={12} className="text-indigo-400" />}
                  </button>
                );
              })}
            </div>
          );
        })}
      {selectedTagIds.length > 0 && (
        <>
          <div className="mx-2.5 my-1.5 border-t border-zinc-700/60" />
          <button
            type="button"
            onClick={onClearAll}
            className="w-full text-left px-3 py-2 text-xs text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors"
          >
            {t('resources.filter.clearSelection', 'Clear selection')}
          </button>
        </>
      )}
    </div>
  );
};
