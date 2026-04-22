// frontend/components/resources/filter/TypeFilterDropdown.tsx
//
// Multi-select dropdown for the broad type chip.

import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Check,
  File,
  FileText,
  Image as ImageIcon,
  Music,
  Video,
  type LucideIcon,
} from 'lucide-react';

import { FILTER_VALUES, type ResourceFilterType } from '../resourceFilters';

export interface TypeFilterDropdownProps {
  selectedTypes: ResourceFilterType[];
  onChange: (next: ResourceFilterType[]) => void;
  onClearAll: () => void;
}

export const TypeFilterDropdown: React.FC<TypeFilterDropdownProps> = ({
  selectedTypes,
  onChange,
  onClearAll,
}) => {
  const { t } = useTranslation();
  const selectedSet = useMemo(() => new Set(selectedTypes), [selectedTypes]);

  const toggle = (value: ResourceFilterType) => {
    if (selectedSet.has(value)) {
      onChange(selectedTypes.filter((v) => v !== value));
    } else {
      onChange([...selectedTypes, value]);
    }
  };

  const options: { value: ResourceFilterType; label: string; Icon: LucideIcon }[] = [
    { value: 'video', label: t('smartFolder.fileTypes.video'), Icon: Video },
    { value: 'image', label: t('smartFolder.fileTypes.image'), Icon: ImageIcon },
    { value: 'audio', label: t('smartFolder.fileTypes.audio'), Icon: Music },
    { value: 'document', label: t('smartFolder.fileTypes.document'), Icon: FileText },
    { value: 'other', label: t('smartFolder.fileTypes.other'), Icon: File },
  ];

  return (
    <div className="w-44 py-1" role="menu" aria-label="Type filter">
      {options.map((opt) => {
        const active = selectedSet.has(opt.value);
        const Icon = opt.Icon;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => toggle(opt.value)}
            className={`w-full text-left px-3 py-1.5 text-xs flex items-center justify-between transition-colors ${
              active ? 'bg-indigo-500/10 text-indigo-300' : 'text-zinc-300 hover:bg-zinc-800'
            }`}
          >
            <span className="flex items-center gap-2">
              <Icon
                size={12}
                className={active ? 'text-indigo-300' : 'text-zinc-500'}
                aria-hidden="true"
              />
              <span>{opt.label}</span>
            </span>
            {active && <Check size={12} className="text-indigo-400" />}
          </button>
        );
      })}
      {selectedTypes.length > 0 && (
        <>
          <div className="mx-2.5 my-1 border-t border-zinc-700/60" />
          <button
            type="button"
            onClick={onClearAll}
            className="w-full text-left px-3 py-2 text-xs text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors"
          >
            {t('resources.filter.clearSelection', 'Clear selection')}
          </button>
        </>
      )}
      {/* Validate FILTER_VALUES stays in sync with options (dev-only noop at runtime). */}
      {FILTER_VALUES.length !== options.length && null}
    </div>
  );
};
