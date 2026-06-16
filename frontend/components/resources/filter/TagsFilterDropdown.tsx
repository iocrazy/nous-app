// frontend/components/resources/filter/TagsFilterDropdown.tsx
//
// Tags filter dropdown for the Resources filter bar. Uses EagleTagBrowser
// so the visual / interaction model matches the "Add Tag" picker used in
// the resource detail panel — category sidebar, starred/frequent sections,
// search, star-on-right-click, layout settings.
//
// Selection semantics at the filter layer are AND — selecting two tags
// matches resources carrying BOTH. The browser itself is a simple multi-
// select; AND/OR is enforced downstream by the filter pipeline.
//
// Tag creation is intentionally hidden here — filters don't need to create
// new tags, so `onCreate` is not passed to the browser.

import React from 'react';
import { useTranslation } from 'react-i18next';

import type { Tag } from '../../../types';
import { islandUI } from '../../../utils/featureFlags';
import { EagleTagBrowser } from '../../EagleTagPicker/EagleTagBrowser';
import { useTagPreferences } from '../../EagleTagPicker/useTagPreferences';

export interface TagsFilterDropdownProps {
  allTags: Tag[];
  selectedTagIds: string[];
  onChange: (nextTagIds: string[]) => void;
  onClearAll: () => void;
  onMergeTags?: (targetId: string, sourceIds: string[]) => Promise<void>;
}

export const TagsFilterDropdown: React.FC<TagsFilterDropdownProps> = ({
  allTags,
  selectedTagIds,
  onChange,
  onClearAll,
  onMergeTags,
}) => {
  const { t } = useTranslation();
  const island = islandUI();
  const { prefs, toggleStar, updateSettings } = useTagPreferences();

  const selectedSet = React.useMemo(() => new Set(selectedTagIds), [selectedTagIds]);

  const handleToggleTag = React.useCallback(
    (tagId: string) => {
      if (selectedSet.has(tagId)) {
        onChange(selectedTagIds.filter((id) => id !== tagId));
      } else {
        onChange([...selectedTagIds, tagId]);
      }
    },
    [selectedSet, selectedTagIds, onChange],
  );

  if (allTags.length === 0) {
    return (
      <div className={`w-64 px-3 py-4 text-xs ${island ? 'text-content-3' : 'text-ink-500'}`} role="menu" aria-label="Tags filter">
        {t('resources.filter.noTags', 'No tags yet')}
      </div>
    );
  }

  return (
    <div
      className="w-[520px] max-w-[90vw] h-[360px] flex flex-col"
      role="menu"
      aria-label="Tags filter"
    >
      <EagleTagBrowser
        allTags={allTags}
        selectedIds={selectedSet}
        starredIds={prefs.starred_tag_ids}
        settings={prefs.picker_settings}
        onToggleTag={handleToggleTag}
        onToggleStar={toggleStar}
        onUpdateSettings={updateSettings}
        onMergeTags={onMergeTags}
        className="flex-1"
      />
      {selectedTagIds.length > 0 && (
        <button
          type="button"
          onClick={onClearAll}
          className={`border-t ${island ? 'border-line' : 'border-ink-800'} px-3 py-2 text-xs ${island ? 'text-content-2 hover:text-content' : 'text-ink-400 hover:text-ink-100'} text-left`}
        >
          {t('resources.filter.clearSelection', 'Clear selection')}
        </button>
      )}
    </div>
  );
};
