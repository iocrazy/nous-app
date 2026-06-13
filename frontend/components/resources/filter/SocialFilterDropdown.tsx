// frontend/components/resources/filter/SocialFilterDropdown.tsx
//
// Multi-metric threshold editor for the Social (interaction data) chip.
// Renders:
//   - combine mode (AND / OR) as a two-way radio
//   - 4 metric rows (likes / comments / favorites / shares) each with a
//     checkbox + number input for the >= threshold
//   - an independent "has comments" checkbox that forces comment_count > 0
//     regardless of the combined thresholds
//
// Semantics: only metrics with ``enabled === true`` contribute to the
// combined filter. A row's threshold defaults to 0 and can be edited
// even while disabled (the value sticks so toggling the checkbox back
// on keeps the user's last choice). ``hasComments`` is always ANDed on
// top.

import React from 'react';
import { useTranslation } from 'react-i18next';
import {
  Bookmark,
  Heart,
  MessageCircle,
  Share2,
  type LucideIcon,
} from 'lucide-react';

import {
  SOCIAL_METRICS,
  type SocialChipValue,
  type SocialMetric,
} from './types';

export interface SocialFilterDropdownProps {
  value: SocialChipValue;
  onChange: (next: SocialChipValue) => void;
  onClearAll: () => void;
}

const METRIC_ICONS: Record<SocialMetric, LucideIcon> = {
  likes: Heart,
  comments: MessageCircle,
  favorites: Bookmark,
  shares: Share2,
};

/** Parse a numeric input value into a non-negative integer. Empty / bad
 *  input falls back to 0 so the row stays in a valid state. */
function parseThreshold(raw: string): number {
  const trimmed = raw.trim();
  if (!trimmed) return 0;
  const n = Number(trimmed);
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.floor(n);
}

export const SocialFilterDropdown: React.FC<SocialFilterDropdownProps> = ({
  value,
  onChange,
  onClearAll,
}) => {
  const { t } = useTranslation();

  const labels: Record<SocialMetric, string> = {
    likes: t('resources.filter.social.likes', 'Likes'),
    comments: t('resources.filter.social.comments', 'Comments'),
    favorites: t('resources.filter.social.favorites', 'Favorites'),
    shares: t('resources.filter.social.shares', 'Shares'),
  };

  const anyEnabled = SOCIAL_METRICS.some((m) => value.metrics[m].enabled);
  const anyActive = anyEnabled || value.hasComments;

  const setCombine = (combine: 'and' | 'or') => {
    if (combine === value.combine) return;
    onChange({ ...value, combine });
  };

  const setMetricEnabled = (metric: SocialMetric, enabled: boolean) => {
    onChange({
      ...value,
      metrics: {
        ...value.metrics,
        [metric]: { ...value.metrics[metric], enabled },
      },
    });
  };

  const setMetricThreshold = (metric: SocialMetric, threshold: number) => {
    onChange({
      ...value,
      metrics: {
        ...value.metrics,
        [metric]: { ...value.metrics[metric], threshold },
      },
    });
  };

  const setHasComments = (hasComments: boolean) => {
    onChange({ ...value, hasComments });
  };

  const combineAndLabel = t(
    'resources.filter.social.combineAnd',
    'All must match (AND)',
  );
  const combineOrLabel = t(
    'resources.filter.social.combineOr',
    'Any matches (OR)',
  );

  return (
    <div className="w-72 py-1.5" role="menu" aria-label="Social filter">
      {/* Combine mode radios */}
      <div
        className="px-3 pt-1 pb-2 border-b border-ink-700/60 space-y-1"
        role="radiogroup"
        aria-label={t('resources.filter.social.combineLabel', 'Combine mode')}
      >
        {(
          [
            ['and', combineAndLabel],
            ['or', combineOrLabel],
          ] as const
        ).map(([mode, label]) => {
          const checked = value.combine === mode;
          return (
            <label
              key={mode}
              className={`flex items-center gap-2 text-xs cursor-pointer px-1 py-1 rounded ${
                checked ? 'text-indigo-300' : 'text-ink-300 hover:text-ink-100'
              }`}
            >
              <input
                type="radio"
                name="social-combine"
                value={mode}
                checked={checked}
                onChange={() => setCombine(mode)}
                className="accent-indigo-500"
              />
              <span>{label}</span>
            </label>
          );
        })}
      </div>

      {/* Metric rows */}
      <div className="px-1 pt-1.5 pb-1 space-y-1">
        {SOCIAL_METRICS.map((metric) => {
          const Icon = METRIC_ICONS[metric];
          const entry = value.metrics[metric];
          const inputId = `filter-social-${metric}`;
          return (
            <div
              key={metric}
              className={`flex items-center gap-2 px-2 py-1 rounded-md ${
                entry.enabled
                  ? 'bg-indigo-500/10'
                  : 'hover:bg-ink-800/60'
              }`}
            >
              <label
                htmlFor={`${inputId}-enabled`}
                className="flex items-center gap-2 text-xs flex-1 cursor-pointer"
              >
                <input
                  id={`${inputId}-enabled`}
                  type="checkbox"
                  checked={entry.enabled}
                  onChange={(e) => setMetricEnabled(metric, e.target.checked)}
                  className="accent-indigo-500"
                />
                <Icon
                  size={12}
                  className={entry.enabled ? 'text-indigo-300' : 'text-ink-500'}
                  aria-hidden="true"
                />
                <span
                  className={entry.enabled ? 'text-indigo-300' : 'text-ink-300'}
                >
                  {labels[metric]}
                </span>
              </label>
              <span
                className="text-[10px] tracking-wider text-ink-500"
                aria-hidden="true"
              >
                {t('resources.filter.social.threshold', '≥')}
              </span>
              <input
                id={inputId}
                type="number"
                min={0}
                inputMode="numeric"
                disabled={!entry.enabled}
                value={entry.threshold}
                onChange={(e) =>
                  setMetricThreshold(metric, parseThreshold(e.target.value))
                }
                className={`w-20 bg-ink-900/60 border rounded px-1.5 py-1 text-[11px] focus:outline-none focus:border-indigo-500 ${
                  entry.enabled
                    ? 'border-ink-600 text-ink-200'
                    : 'border-ink-800 text-ink-500'
                }`}
                aria-label={`${labels[metric]} threshold`}
              />
            </div>
          );
        })}
      </div>

      {/* Has-comments floor */}
      <div className="px-3 py-2 border-t border-ink-700/60">
        <label className="flex items-center gap-2 text-xs cursor-pointer text-ink-200">
          <input
            type="checkbox"
            checked={value.hasComments}
            onChange={(e) => setHasComments(e.target.checked)}
            className="accent-indigo-500"
          />
          <MessageCircle
            size={12}
            className={value.hasComments ? 'text-indigo-300' : 'text-ink-500'}
            aria-hidden="true"
          />
          <span>
            {t('resources.filter.social.hasComments', 'Has comments')}
          </span>
        </label>
      </div>

      {anyActive && (
        <>
          <div className="mx-2.5 my-1 border-t border-ink-700/60" />
          <button
            type="button"
            onClick={onClearAll}
            className="w-full text-left px-3 py-2 text-xs text-ink-500 hover:text-ink-300 hover:bg-ink-800 transition-colors"
          >
            {t('resources.filter.clearSelection', 'Clear selection')}
          </button>
        </>
      )}
    </div>
  );
};
