// frontend/components/Inspiration/NoteTagsFilterDropdown.tsx
//
// Note-tag picker for the Inspiration filter row. Row shape is copied from
// `resources/filter/TypeFilterDropdown` so the two bars read as one system.
//
// SINGLE-SELECT: the backend takes one optional `tag` string
// (`inspiration_router.py`, `NoteFilters.tag?: string`). A multi-select
// control here would let the user pick three tags and silently apply one.
// Re-picking the selected tag clears it — the same toggle the sidebar
// TagsPanel already has.
//
// NOT `resources/filter/TagsFilterDropdown`: that one is keyed by `Tag` entity
// UUIDs and mounts a 520x360 EagleTagBrowser. Note tags are bare strings in a
// `TEXT[]` column carrying their own counts — a different domain, not a
// narrower skin of the same one.

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Hash } from 'lucide-react';

export interface NoteTagsFilterDropdownProps {
  /** Tag frequency rows straight from `getTagCounts()`. */
  tags: { tag: string; cnt: number }[];
  /** Currently filtered tag, or null. */
  activeTag: string | null;
  /** Receives the new tag, or null when the selection is toggled off. */
  onChange: (next: string | null) => void;
}

export const NoteTagsFilterDropdown: React.FC<NoteTagsFilterDropdownProps> = ({
  tags,
  activeTag,
  onChange,
}) => {
  const { t } = useTranslation();

  return (
    <div
      className="w-max min-w-[11rem] max-w-[22rem] max-h-80 overflow-y-auto py-1"
      role="menu"
      aria-label="Note tags filter"
    >
      {tags.length === 0 && (
        <div className="px-3 py-4 text-xs text-content-3">
          {t('inspiration.noTagsYet', 'No tags yet')}
        </div>
      )}
      {tags.map(({ tag, cnt }) => {
        const active = tag === activeTag;
        return (
          <button
            key={tag}
            type="button"
            onClick={() => onChange(active ? null : tag)}
            className={`w-full text-left px-3 py-1.5 text-xs flex items-center justify-between gap-3 transition-colors ${
              active
                ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                : 'text-content-2 hover:bg-island-2'
            }`}
          >
            <span className="flex items-center gap-2 truncate">
              <Hash
                size={12}
                className={active ? 'text-[var(--accent-text)]' : 'text-content-3'}
                aria-hidden="true"
              />
              <span className="truncate">{tag}</span>
            </span>
            <span className="flex shrink-0 items-center gap-2">
              <span className={active ? 'text-[var(--accent-text)]' : 'text-content-4'}>
                {cnt}
              </span>
              {active && <Check size={12} className="text-[var(--accent-text)]" />}
            </span>
          </button>
        );
      })}
    </div>
  );
};
