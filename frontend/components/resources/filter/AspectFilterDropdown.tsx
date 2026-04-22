// frontend/components/resources/filter/AspectFilterDropdown.tsx
//
// Multi-select checkbox list for the Aspect chip. Each selected bucket
// maps to a width/height ratio range applied client-side in
// useResourcesDisplay. Video-specific — resources without a parseable
// resolution string get filtered out when any bucket is active.

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Check } from 'lucide-react';

import type { AspectBucketId } from './types';

export interface AspectFilterDropdownProps {
  selectedBuckets: AspectBucketId[];
  onChange: (next: AspectBucketId[]) => void;
  onClearAll: () => void;
}

const BUCKET_ORDER: AspectBucketId[] = [
  'portrait',
  'landscape',
  'square',
  'fourThree',
  'other',
];

export const AspectFilterDropdown: React.FC<AspectFilterDropdownProps> = ({
  selectedBuckets,
  onChange,
  onClearAll,
}) => {
  const { t } = useTranslation();
  const selectedSet = React.useMemo(
    () => new Set(selectedBuckets),
    [selectedBuckets],
  );

  const labels: Record<AspectBucketId, string> = {
    portrait: t('resources.filter.aspectBuckets.portrait', '9:16 (Portrait)'),
    landscape: t('resources.filter.aspectBuckets.landscape', '16:9 (Landscape)'),
    square: t('resources.filter.aspectBuckets.square', '1:1 (Square)'),
    fourThree: t('resources.filter.aspectBuckets.fourThree', '4:3'),
    other: t('resources.filter.aspectBuckets.other', 'Other'),
  };

  const toggle = (bucket: AspectBucketId) => {
    if (selectedSet.has(bucket)) {
      onChange(selectedBuckets.filter((b) => b !== bucket));
    } else {
      onChange([...selectedBuckets, bucket]);
    }
  };

  return (
    <div className="w-56 py-1" role="menu" aria-label="Aspect filter">
      {BUCKET_ORDER.map((bucket) => {
        const active = selectedSet.has(bucket);
        return (
          <button
            key={bucket}
            type="button"
            onClick={() => toggle(bucket)}
            className={`w-full text-left px-3 py-1.5 text-xs flex items-center justify-between transition-colors ${
              active
                ? 'bg-indigo-500/10 text-indigo-300'
                : 'text-zinc-300 hover:bg-zinc-800'
            }`}
          >
            <span>{labels[bucket]}</span>
            {active && <Check size={12} className="text-indigo-400" />}
          </button>
        );
      })}
      {selectedBuckets.length > 0 && (
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
