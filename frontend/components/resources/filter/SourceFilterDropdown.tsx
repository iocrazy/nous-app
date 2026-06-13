// frontend/components/resources/filter/SourceFilterDropdown.tsx
//
// Multi-select dropdown for the Source (platform) chip. OR semantics —
// a resource matches if its parsed_media.source_platform is any of the
// selected platforms. Options are a union of known platforms and any
// platforms observed in the currently-loaded resource set, so rarely
// seen sources still show up for filtering.

import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Check } from 'lucide-react';

/** Canonical ordered list of known source platforms. */
const KNOWN_PLATFORMS: string[] = [
  'douyin',
  'xiaohongshu',
  'bilibili',
  'youtube',
  'tiktok',
  'twitter',
  'upload',
  'other',
];

const PLATFORM_LABELS: Record<string, string> = {
  douyin: 'Douyin',
  xiaohongshu: 'Xiaohongshu',
  bilibili: 'Bilibili',
  youtube: 'YouTube',
  tiktok: 'TikTok',
  twitter: 'X',
  upload: 'Upload',
  other: 'Other',
};

function platformLabel(platform: string): string {
  return (
    PLATFORM_LABELS[platform] ||
    platform.charAt(0).toUpperCase() + platform.slice(1)
  );
}

export interface SourceFilterDropdownProps {
  selectedPlatforms: string[];
  onChange: (next: string[]) => void;
  onClearAll: () => void;
  /** Additional platforms observed in the loaded resource set so they
   *  still appear as options even if not in KNOWN_PLATFORMS. */
  availablePlatforms?: string[];
}

export const SourceFilterDropdown: React.FC<SourceFilterDropdownProps> = ({
  selectedPlatforms,
  onChange,
  onClearAll,
  availablePlatforms = [],
}) => {
  const { t } = useTranslation();
  const selectedSet = useMemo(
    () => new Set(selectedPlatforms),
    [selectedPlatforms],
  );

  const options = useMemo<string[]>(() => {
    const seen = new Set<string>();
    const result: string[] = [];
    for (const p of [...KNOWN_PLATFORMS, ...availablePlatforms]) {
      const normalised = (p || '').trim();
      if (!normalised) continue;
      if (seen.has(normalised)) continue;
      seen.add(normalised);
      result.push(normalised);
    }
    return result;
  }, [availablePlatforms]);

  const toggle = (platform: string) => {
    if (selectedSet.has(platform)) {
      onChange(selectedPlatforms.filter((p) => p !== platform));
    } else {
      onChange([...selectedPlatforms, platform]);
    }
  };

  return (
    <div
      className="w-52 max-h-80 overflow-y-auto py-1"
      role="menu"
      aria-label="Source filter"
    >
      {options.length === 0 && (
        <div className="px-3 py-4 text-xs text-ink-500">
          {t('resources.filter.noSources', 'No sources yet')}
        </div>
      )}
      {options.map((platform) => {
        const active = selectedSet.has(platform);
        const iconOk = [
          'douyin',
          'bilibili',
          'youtube',
          'tiktok',
          'xiaohongshu',
          'twitter',
        ].includes(platform);
        return (
          <button
            key={platform}
            type="button"
            onClick={() => toggle(platform)}
            className={`w-full text-left px-3 py-1.5 text-xs flex items-center justify-between transition-colors ${
              active
                ? 'bg-indigo-500/10 text-indigo-300'
                : 'text-ink-300 hover:bg-ink-800'
            }`}
          >
            <span className="flex items-center gap-2 truncate">
              {iconOk ? (
                <img
                  src={`/icons/${platform}.svg`}
                  alt=""
                  className="w-3.5 h-3.5 shrink-0"
                />
              ) : (
                <span className="inline-block w-3.5 h-3.5 shrink-0" />
              )}
              <span className="truncate">{platformLabel(platform)}</span>
            </span>
            {active && <Check size={12} className="text-indigo-400" />}
          </button>
        );
      })}
      {selectedPlatforms.length > 0 && (
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

export { platformLabel };
