// frontend/components/resources/filter/DateAddedFilterDropdown.tsx
//
// Single-select radio list of preset date ranges for the Date-added
// chip, plus an inline Custom-range editor that opens the app-wide
// DateTimePopover in range mode. Selecting a preset other than "custom"
// auto-clears the custom bounds so the chip state stays consistent.
//
// The popover portals to document.body — see FilterChip's `inFloatingLayer`
// guard, which is what keeps a click on a calendar day from reading as an
// outside click and closing this dropdown out from under the picker.

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Calendar, CalendarRange, Check, Clock, type LucideIcon } from 'lucide-react';

import { DateTimePopover } from '../../common/DateTimePopover';
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
  const [rangeAnchor, setRangeAnchor] = useState<HTMLElement | null>(null);
  const inactiveRow = 'text-content-2 hover:bg-island-2';
  const inactiveIcon = 'text-content-3';
  const labelMuted = 'text-content-3';
  const dateTriggerCls = 'flex-1 truncate bg-island-2 border border-line rounded px-1.5 py-1 text-left text-[11px] text-content-2 transition-colors hover:border-line-strong focus:outline-none focus:border-line-strong';

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

  // The popover is commit-on-complete: one onChange carries BOTH ends, where
  // the two native inputs each committed their own field. The emitted shape is
  // unchanged — same three keys, same `null` for "unset".
  const setCustomRange = (after: string | null, before: string | null) => {
    onChange({
      preset: 'custom',
      customAfter: after || null,
      customBefore: before || null,
    });
  };

  const isActive = (preset: DatePresetId) => value.preset === preset;

  return (
    <div className="w-max min-w-[12rem] py-1" role="menu" aria-label="Date added filter">
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
                ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                : inactiveRow
            }`}
          >
            <span className="flex items-center gap-2">
              <Icon
                size={12}
                className={active ? 'text-[var(--accent-text)]' : inactiveIcon}
                aria-hidden="true"
              />
              <span>{labels[preset]}</span>
            </span>
            {active && <Check size={12} className="text-[var(--accent-text)]" />}
          </button>
        );
      })}
      {value.preset === 'custom' && (
        <div className="px-3 py-2 space-y-1.5 border-t border-line mt-1">
          <div className="flex items-center gap-2">
            <label
              htmlFor="filter-date-after"
              className={`text-[10px] uppercase tracking-wider ${labelMuted} w-8 shrink-0`}
            >
              {t('resources.filter.date.from', 'From')}
            </label>
            <button
              id="filter-date-after"
              type="button"
              data-testid="filter-date-after"
              aria-label={t('common.dateRangePopover.title', 'Select date range')}
              onClick={(e) => setRangeAnchor(e.currentTarget)}
              className={dateTriggerCls}
            >
              {value.customAfter ?? t('common.dateRangePopover.empty', '–')}
            </button>
          </div>
          <div className="flex items-center gap-2">
            <label
              htmlFor="filter-date-before"
              className={`text-[10px] uppercase tracking-wider ${labelMuted} w-8 shrink-0`}
            >
              {t('resources.filter.date.to', 'To')}
            </label>
            <button
              id="filter-date-before"
              type="button"
              data-testid="filter-date-before"
              aria-label={t('common.dateRangePopover.title', 'Select date range')}
              onClick={(e) => setRangeAnchor(e.currentTarget)}
              className={dateTriggerCls}
            >
              {value.customBefore ?? t('common.dateRangePopover.empty', '–')}
            </button>
          </div>
          {/* Both triggers open the same range picker — whichever was clicked
              is the anchor. */}
          <DateTimePopover
            anchorEl={rangeAnchor}
            start={value.customAfter}
            end={value.customBefore}
            onChange={setCustomRange}
            onClose={() => setRangeAnchor(null)}
          />
        </div>
      )}
      {value.preset !== null && (
        <>
          <div className="mx-2.5 my-1 border-t border-line" />
          <button
            type="button"
            onClick={onClearAll}
            className="w-full text-left px-3 py-2 text-xs text-content-3 hover:text-content-2 hover:bg-island-2 transition-colors"
          >
            {t('resources.filter.clearSelection', 'Clear selection')}
          </button>
        </>
      )}
    </div>
  );
};
