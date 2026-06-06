// frontend/components/filters/FacetDropdown.tsx
//
// Compact anchored dropdown for a BOUNDED filter dimension (Type / Source / AI
// / Date / Duration / Aspect / Rating). A handful of options doesn't deserve a
// full screen — show them inline in a popover hung off the chip (Pixcall's
// Rating-dropdown pattern). The unbounded Tags and the multi-control Social
// facet use the full-screen FacetPickerSheet instead.

import { createPortal } from 'react-dom';
import { Check, XCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { UseFilterBarConfigReturn } from '../../hooks/useFilterBarConfig';
import type { ChipId } from '../resources/filter/types';
import {
  TYPE_OPTIONS,
  KNOWN_PLATFORMS,
  platformLabel,
  AI_FLAGS,
  DATE_PRESETS,
  DURATION_PRESETS,
  ASPECT_OPTIONS,
} from './facetMeta';

interface OptionRow {
  key: string;
  label: string;
  selected: boolean;
  onToggle: () => void;
}

function toggleIn<T>(arr: readonly T[], v: T): T[] {
  return arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v];
}

/** Build the option rows for a bounded facet from the shared config. */
function buildRows(
  facetId: ChipId,
  config: UseFilterBarConfigReturn,
  availablePlatforms: string[],
): OptionRow[] {
  const { chipValues, setChipValue } = config;
  switch (facetId) {
    case 'type':
      return TYPE_OPTIONS.map((o) => ({
        key: o.id,
        label: o.label,
        selected: chipValues.type.types.includes(o.id),
        onToggle: () =>
          setChipValue('type', { types: toggleIn(chipValues.type.types, o.id) }),
      }));
    case 'source':
      return Array.from(
        new Set<string>([...KNOWN_PLATFORMS, ...availablePlatforms]),
      ).map((p) => ({
        key: p,
        label: platformLabel(p),
        selected: chipValues.source.platforms.includes(p),
        onToggle: () =>
          setChipValue('source', {
            platforms: toggleIn(chipValues.source.platforms, p),
          }),
      }));
    case 'ai_status':
      return AI_FLAGS.map((f) => ({
        key: f.key,
        label: f.label,
        selected: chipValues.ai_status[f.key],
        onToggle: () =>
          setChipValue('ai_status', {
            ...chipValues.ai_status,
            [f.key]: !chipValues.ai_status[f.key],
          }),
      }));
    case 'date_added':
      return DATE_PRESETS.map((d) => {
        const selected = chipValues.date_added.preset === d.id;
        return {
          key: d.id,
          label: d.label,
          selected,
          onToggle: () =>
            setChipValue('date_added', {
              preset: selected ? null : d.id,
              customAfter: null,
              customBefore: null,
            }),
        };
      });
    case 'duration':
      return DURATION_PRESETS.map((d) => {
        const selected = chipValues.duration.preset === d.id;
        return {
          key: d.id,
          label: d.label,
          selected,
          onToggle: () =>
            setChipValue('duration', {
              preset: selected ? null : d.id,
              customMin: null,
              customMax: null,
            }),
        };
      });
    case 'aspect':
      return ASPECT_OPTIONS.map((o) => ({
        key: o.id,
        label: o.label,
        selected: chipValues.aspect.buckets.includes(o.id),
        onToggle: () =>
          setChipValue('aspect', {
            buckets: toggleIn(chipValues.aspect.buckets, o.id),
          }),
      }));
    case 'rating':
      return [5, 4, 3, 2, 1].map((n) => {
        const selected = chipValues.rating.min_rating === n;
        return {
          key: String(n),
          label: `${'★'.repeat(n)}  ${n}+`,
          selected,
          onToggle: () =>
            setChipValue('rating', { min_rating: selected ? 0 : n }),
        };
      });
    default:
      return [];
  }
}

export function FacetDropdown({
  facetId,
  anchorRect,
  config,
  availablePlatforms = [],
  onClose,
}: {
  facetId: ChipId;
  anchorRect: DOMRect;
  config: UseFilterBarConfigReturn;
  availablePlatforms?: string[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const rows = buildRows(facetId, config, availablePlatforms);

  const WIDTH = 240;
  const vw = typeof window !== 'undefined' ? window.innerWidth : 390;
  const left = Math.min(Math.max(8, anchorRect.left), vw - WIDTH - 8);
  const top = anchorRect.bottom + 6;

  return createPortal(
    <div className="md:hidden fixed inset-0 z-[65]" onClick={onClose}>
      <div
        className="absolute rounded-2xl bg-zinc-800/95 backdrop-blur border border-zinc-700 shadow-2xl overflow-hidden py-1 animate-in fade-in slide-in-from-top-1 duration-150"
        style={{ left, top, width: WIDTH, maxHeight: '60vh', overflowY: 'auto' }}
        onClick={(e) => e.stopPropagation()}
      >
        {rows.map((r) => (
          <button
            key={r.key}
            type="button"
            onClick={r.onToggle}
            className={`w-full flex items-center gap-3 px-4 py-2.5 text-left text-sm transition-colors ${
              r.selected ? 'text-white bg-indigo-500/10' : 'text-zinc-300'
            } active:bg-zinc-700`}
          >
            <span className="w-4 shrink-0 text-indigo-400">
              {r.selected && (
                <Check size={15} className="animate-in zoom-in-50 duration-150" />
              )}
            </span>
            <span className="flex-1 truncate">{r.label}</span>
          </button>
        ))}
        <div className="border-t border-zinc-700/60 mt-1">
          <button
            type="button"
            onClick={() => {
              config.clearChip(facetId);
              onClose();
            }}
            className="w-full flex items-center gap-2 px-4 py-2.5 text-sm text-zinc-400 active:bg-zinc-700"
          >
            <XCircle size={15} />
            {t('resources.filter.clear', 'Clear')}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
