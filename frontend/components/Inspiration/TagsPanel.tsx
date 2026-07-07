// Sidebar tag frequency list (spec §2.2 #3): click toggles the active tag
// filter shared with the page; re-clicking the active tag clears it.
import React from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';

interface Props {
  tags: { tag: string; cnt: number }[];
  activeTag: string | null;
  onTagClick: (tag: string | null) => void;
}

export const TagsPanel: React.FC<Props> = ({ tags, activeTag, onTagClick }) => {
  const { t } = useTranslation();
  if (!tags.length) return null;
  return (
    <div className="rounded-xl bg-island px-4 py-3.5">
      <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider text-content-3">
        {t('inspiration.tags', 'Tags')}
      </h3>
      <div className="flex flex-wrap gap-1.5">
        {tags.map(({ tag, cnt }) => {
          const on = tag === activeTag;
          return (
            <button
              key={tag}
              onClick={() => onTagClick(on ? null : tag)}
              className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs ${
                on ? 'bg-indigo-500/20 text-indigo-300' : 'bg-island-2 text-content-2 hover:bg-line'
              }`}
            >
              {`#${tag} (${cnt})`}
              {/* Decorative — the whole button already toggles the filter
                  off on click, and the topbar chip owns the single
                  "Clear tag filter" affordance for screen readers. */}
              {on && <X size={10} aria-hidden="true" />}
            </button>
          );
        })}
      </div>
    </div>
  );
};
