// frontend/components/resources/filter/RatingFilterDropdown.tsx
//
// Single-select dropdown for the minimum rating filter. Choices are
// {≥5, ≥4, ≥3, ≥2, ≥1}. Selecting "All" clears the filter.

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Star } from 'lucide-react';

import { islandUI } from '../../../utils/featureFlags';

export interface RatingFilterDropdownProps {
  /** 0 = inactive, 1..5 = minimum rating. */
  minRating: number;
  onChange: (next: number) => void;
}

const CHOICES = [5, 4, 3, 2, 1] as const;

export const RatingFilterDropdown: React.FC<RatingFilterDropdownProps> = ({
  minRating,
  onChange,
}) => {
  const { t } = useTranslation();
  const island = islandUI();
  const inactiveRow = island
    ? 'text-content-2 hover:bg-island-2'
    : 'text-ink-300 hover:bg-ink-800';

  // Width hugs the widest option (≥ plus 5 stars + Check); previously
  // a fixed w-44 left a big empty strip on the right of each row.
  return (
    <div className="py-1 w-max min-w-[9rem]" role="menu" aria-label="Rating filter">
      <button
        type="button"
        onClick={() => onChange(0)}
        className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-3 transition-colors ${
          minRating === 0 ? 'bg-indigo-500/10 text-indigo-300' : inactiveRow
        }`}
      >
        <span className="flex-1">{t('resources.filter.anyRating', 'Any rating')}</span>
        {minRating === 0 && <Check size={12} className="text-indigo-400 shrink-0" />}
      </button>
      <div className={`mx-2.5 my-1 border-t ${island ? 'border-line' : 'border-ink-700/60'}`} />
      {CHOICES.map((value) => {
        const active = minRating === value;
        return (
          <button
            key={value}
            type="button"
            onClick={() => onChange(value)}
            className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-3 transition-colors ${
              active ? 'bg-indigo-500/10 text-indigo-300' : inactiveRow
            }`}
          >
            <span className="flex items-center gap-1">
              <span className={`${island ? 'text-content-2' : 'text-ink-400'} mr-1`}>{'≥'}</span>
              {Array.from({ length: value }).map((_, i) => (
                <Star
                  key={i}
                  size={11}
                  className={active ? 'text-amber-300' : 'text-amber-400/70'}
                  fill="currentColor"
                />
              ))}
            </span>
            {active && <Check size={12} className="text-indigo-400 shrink-0 ml-auto" />}
          </button>
        );
      })}
    </div>
  );
};
