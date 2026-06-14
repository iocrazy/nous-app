// frontend/components/resources/filter/SegmentedTypeFilter.tsx
//
// Island redesign v2 — segmented media-type filter for the resources toolbar
// (docs/design/mockups/mediahub-resources-redesign-v2.html `.seg`). Renders an
// always-visible connected control [All · Images · Video · Audio · Docs · Other]
// in place of the `type` chip dropdown when the island shell is active.
//
// It is a pure alternate surface over the SAME multi-select type-filter state
// (`chipValues.type.types`), so no filtering capability is added or removed
// (D12): clicking a type toggles it in/out of the selection (multi-select), and
// "All" clears the selection. Only mounted in island mode; classic keeps the
// dropdown chip.

import React from 'react';
import { useTranslation } from 'react-i18next';
import {
  File,
  FileText,
  Image as ImageIcon,
  Music,
  Video,
  type LucideIcon,
} from 'lucide-react';

import type { ResourceFilterType } from '../resourceFilters';

export interface SegmentedTypeFilterProps {
  /** Currently selected types (empty = "All"). */
  selected: ResourceFilterType[];
  /** Replace the selection. Empty array clears (back to "All"). */
  onChange: (next: ResourceFilterType[]) => void;
}

const SEGMENTS: { value: ResourceFilterType; labelKey: string; fallback: string; Icon: LucideIcon }[] = [
  { value: 'image', labelKey: 'smartFolder.fileTypes.image', fallback: 'Images', Icon: ImageIcon },
  { value: 'video', labelKey: 'smartFolder.fileTypes.video', fallback: 'Video', Icon: Video },
  { value: 'audio', labelKey: 'smartFolder.fileTypes.audio', fallback: 'Audio', Icon: Music },
  { value: 'document', labelKey: 'smartFolder.fileTypes.document', fallback: 'Docs', Icon: FileText },
  { value: 'other', labelKey: 'smartFolder.fileTypes.other', fallback: 'Other', Icon: File },
];

export const SegmentedTypeFilter: React.FC<SegmentedTypeFilterProps> = ({ selected, onChange }) => {
  const { t } = useTranslation();
  const selectedSet = new Set(selected);
  const allActive = selected.length === 0;

  const toggle = (value: ResourceFilterType) => {
    if (selectedSet.has(value)) {
      onChange(selected.filter((v) => v !== value));
    } else {
      onChange([...selected, value]);
    }
  };

  const segClass = (active: boolean) =>
    `inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium whitespace-nowrap transition-colors ${
      active ? 'bg-indigo-500/15 text-indigo-300' : 'text-ink-400 hover:text-ink-200'
    }`;

  return (
    <div
      className="inline-flex items-center gap-0.5 p-[3px] rounded-lg border border-line bg-island-2"
      role="group"
      aria-label={t('resources.filter.typeLabel', 'Type')}
      data-testid="segmented-type-filter"
    >
      <button
        type="button"
        onClick={() => onChange([])}
        className={segClass(allActive)}
        aria-pressed={allActive}
      >
        {t('resources.filter.type.all', 'All')}
      </button>
      {SEGMENTS.map(({ value, labelKey, fallback, Icon }) => {
        const active = selectedSet.has(value);
        return (
          <button
            key={value}
            type="button"
            onClick={() => toggle(value)}
            className={segClass(active)}
            aria-pressed={active}
          >
            <Icon size={13} aria-hidden="true" />
            <span>{t(labelKey, fallback)}</span>
          </button>
        );
      })}
    </div>
  );
};
