// frontend/components/resources/filter/DateAddedFilterDropdown.tsx
//
// Single-select radio list of preset date ranges for the Date-added
// chip, plus an inline Custom-range editor with two native <input
// type="date"> fields. Selecting a preset other than "custom" auto-
// clears the custom inputs so the chip state stays consistent.

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Calendar, CalendarRange, Check, Clock, type LucideIcon } from 'lucide-react';

import type { DateAddedChipValue, DatePresetId } from './types';

export interface DateAddedFilterDropdownProps {
  value: DateAddedChipValue;
  onChange: (next: DateAddedChipValue) => void;
  onClearAll: () => void;
}

const PRESETS: DatePresetId[] = [
  'today',
  'thisWeek',
  'thisMonth',
  'last30days',
  'last90days',
  'custom',
];

export const DateAddedFilterDropdown: React.FC<DateAddedFilterDropdownProps> = ({
  value,
  onChange,
  onClearAll,
}) => {
  const { t } = useTranslation();

  const labels: Record<DatePresetId, string> = {
    today: t('resources.filter.date.today', 'Today'),
    thisWeek: t('resources.filter.date.thisWeek', 'This week'),
    thisMonth: t('resources.filter.date.thisMonth', 'This month'),
    last30days: t('resources.filter.date.last30days', 'Last 30 days'),
    last90days: t('resources.filter.date.last90days', 'Last 90 days'),
    custom: t('resources.filter.date.custom', 'Custom range'),
  };

  const icons: Record<DatePresetId, LucideIcon> = {
    today: Clock,
    thisWeek: Calendar,
    thisMonth: Calendar,
    last30days: Calendar,
    last90days: Calendar,
    custom: CalendarRange,
  };

  const selectPreset = (preset: DatePresetId) => {
    if (preset === 'custom') {
      onChange({
        preset: 'custom',
        customAfter: value.customAfter,
        customBefore: value.customBefore,
      });
    } else {
      // Switching to a non-custom preset clears custom bounds to keep
      // toFilterParams / summary honest.
      onChange({ preset, customAfter: null, customBefore: null });
    }
  };

  const setCustom = (
    field: 'customAfter' | 'customBefore',
    raw: string,
  ) => {
    const next: DateAddedChipValue = {
      preset: 'custom',
      customAfter: value.customAfter,
      customBefore: value.customBefore,
      [field]: raw || null,
    };
    onChange(next);
  };

  const isActive = (preset: DatePresetId) => value.preset === preset;

  return (
    <div className="w-56 py-1" role="menu" aria-label="Date added filter">
      {PRESETS.map((preset) => {
        const active = isActive(preset);
        const Icon = icons[preset];
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
            <span className="flex items-center gap-2">
              <Icon
                size={12}
                className={active ? 'text-indigo-300' : 'text-zinc-500'}
                aria-hidden="true"
              />
              <span>{labels[preset]}</span>
            </span>
            {active && <Check size={12} className="text-indigo-400" />}
          </button>
        );
      })}
      {value.preset === 'custom' && (
        <div className="px-3 py-2 space-y-1.5 border-t border-zinc-700/60 mt-1">
          <div className="flex items-center gap-2">
            <label
              htmlFor="filter-date-after"
              className="text-[10px] uppercase tracking-wider text-zinc-500 w-8 shrink-0"
            >
              {t('resources.filter.date.from', 'From')}
            </label>
            <input
              id="filter-date-after"
              type="date"
              value={value.customAfter ?? ''}
              onChange={(e) => setCustom('customAfter', e.target.value)}
              className="flex-1 bg-zinc-900/60 border border-zinc-700 rounded px-1.5 py-1 text-[11px] text-zinc-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div className="flex items-center gap-2">
            <label
              htmlFor="filter-date-before"
              className="text-[10px] uppercase tracking-wider text-zinc-500 w-8 shrink-0"
            >
              {t('resources.filter.date.to', 'To')}
            </label>
            <input
              id="filter-date-before"
              type="date"
              value={value.customBefore ?? ''}
              onChange={(e) => setCustom('customBefore', e.target.value)}
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
