// frontend/components/resources/filter/DurationFilterDropdown.tsx
//
// Single-select radio list of preset duration ranges for the Duration
// chip, plus an inline Custom-range editor with two number inputs
// (min / max seconds). Selecting a preset other than "custom" auto-
// clears the custom inputs so the chip state stays consistent.
//
// This chip is video-specific — resources without a duration_seconds
// value get filtered out when any range is active (including a wide
// custom range). That's intentional: filtering by duration is only
// meaningful for media.

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Check } from 'lucide-react';

import type { DurationChipValue, DurationPresetId } from './types';

export interface DurationFilterDropdownProps {
  value: DurationChipValue;
  onChange: (next: DurationChipValue) => void;
  onClearAll: () => void;
}

const PRESETS: DurationPresetId[] = [
  'short60s',
  'medium',
  'long',
  'xlong',
  'custom',
];

export const DurationFilterDropdown: React.FC<DurationFilterDropdownProps> = ({
  value,
  onChange,
  onClearAll,
}) => {
  const { t } = useTranslation();

  const labels: Record<DurationPresetId, string> = {
    short60s: t('resources.filter.duration.short60s', '≤ 60 seconds'),
    medium: t('resources.filter.duration.medium', '1-5 minutes'),
    long: t('resources.filter.duration.long', '5-30 minutes'),
    xlong: t('resources.filter.duration.xlong', '30+ minutes'),
    custom: t('resources.filter.duration.custom', 'Custom range'),
  };

  const selectPreset = (preset: DurationPresetId) => {
    if (preset === 'custom') {
      onChange({
        preset: 'custom',
        customMin: value.customMin,
        customMax: value.customMax,
      });
    } else {
      // Switching to a non-custom preset clears custom bounds to keep
      // summary / params honest.
      onChange({ preset, customMin: null, customMax: null });
    }
  };

  const setCustom = (field: 'customMin' | 'customMax', raw: string) => {
    const trimmed = raw.trim();
    let parsed: number | null = null;
    if (trimmed.length > 0) {
      const asNumber = Number(trimmed);
      if (Number.isFinite(asNumber) && asNumber >= 0) {
        parsed = Math.floor(asNumber);
      }
    }
    onChange({
      preset: 'custom',
      customMin: value.customMin,
      customMax: value.customMax,
      [field]: parsed,
    });
  };

  const isActive = (preset: DurationPresetId) => value.preset === preset;

  return (
    <div className="w-56 py-1" role="menu" aria-label="Duration filter">
      {PRESETS.map((preset) => {
        const active = isActive(preset);
        return (
          <button
            key={preset}
            type="button"
            onClick={() => selectPreset(preset)}
            className={`w-full text-left px-3 py-1.5 text-xs flex items-center justify-between transition-colors ${
              active
                ? 'bg-indigo-500/10 text-indigo-300'
                : 'text-zinc-300 hover:bg-zinc-800'
            }`}
          >
            <span>{labels[preset]}</span>
            {active && <Check size={12} className="text-indigo-400" />}
          </button>
        );
      })}
      {value.preset === 'custom' && (
        <div className="px-3 py-2 space-y-1.5 border-t border-zinc-700/60 mt-1">
          <div className="flex items-center gap-2">
            <label
              htmlFor="filter-duration-min"
              className="text-[10px] uppercase tracking-wider text-zinc-500 w-8 shrink-0"
            >
              {t('resources.filter.duration.min', 'Min (seconds)')}
            </label>
            <input
              id="filter-duration-min"
              type="number"
              min={0}
              inputMode="numeric"
              value={value.customMin ?? ''}
              onChange={(e) => setCustom('customMin', e.target.value)}
              className="flex-1 bg-zinc-900/60 border border-zinc-700 rounded px-1.5 py-1 text-[11px] text-zinc-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div className="flex items-center gap-2">
            <label
              htmlFor="filter-duration-max"
              className="text-[10px] uppercase tracking-wider text-zinc-500 w-8 shrink-0"
            >
              {t('resources.filter.duration.max', 'Max (seconds)')}
            </label>
            <input
              id="filter-duration-max"
              type="number"
              min={0}
              inputMode="numeric"
              value={value.customMax ?? ''}
              onChange={(e) => setCustom('customMax', e.target.value)}
              className="flex-1 bg-zinc-900/60 border border-zinc-700 rounded px-1.5 py-1 text-[11px] text-zinc-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
        </div>
      )}
      {value.preset !== null && (
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
    </div>
  );
};
