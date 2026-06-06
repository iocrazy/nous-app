// frontend/components/filters/FilterChipBar.tsx
//
// Pixcall-style always-visible filter chip row (mobile). Layout:
//   [≡ N]  [applied value chips ✕ …]  [unapplied dimension chips ▾ …]
// - The leading ≡ chip shows the active count and opens a "N applied / Clear
//   all" menu.
// - An APPLIED facet renders as a solid chip showing its value, tapping the
//   body re-opens its picker, tapping the ✕ clears just that facet.
// - An UNAPPLIED facet renders as a muted chip that opens its picker.
// Bounded facets open a compact FacetDropdown anchored to the chip; the
// unbounded Tags / multi-control Social facets open the full-screen
// FacetPickerSheet via onOpenFacet.
//
// Controlled by the SAME useFilterBarConfig instance the view owns (props).

import { useState } from 'react';
import { SlidersHorizontal, ChevronDown, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { UseFilterBarConfigReturn } from '../../hooks/useFilterBarConfig';
import type { ChipId } from '../resources/filter/types';
import type { Tag } from '../../types';
import { FACETS, facetActiveLabel, facetIsFullscreen } from './facetMeta';
import { FacetDropdown } from './FacetDropdown';

interface FilterChipBarProps {
  config: UseFilterBarConfigReturn;
  allTags: Tag[];
  availablePlatforms?: string[];
  /** Open the full-screen picker for a facet (Tags / Social). */
  onOpenFacet: (id: ChipId) => void;
  className?: string;
}

export function FilterChipBar({
  config,
  allTags,
  availablePlatforms = [],
  onOpenFacet,
  className = '',
}: FilterChipBarProps) {
  const { t } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [dropdown, setDropdown] = useState<{ id: ChipId; rect: DOMRect } | null>(
    null,
  );

  const { chipValues, isChipActive, activeFilterCount, clearAll, clearChip } =
    config;

  const openFacet = (id: ChipId, e: React.MouseEvent<HTMLButtonElement>) => {
    if (facetIsFullscreen(id)) {
      onOpenFacet(id);
    } else {
      setDropdown({ id, rect: e.currentTarget.getBoundingClientRect() });
    }
  };

  // Active facets first, then the rest — mirrors Pixcall's dynamic reorder.
  const active = FACETS.filter((f) => isChipActive(f.id));
  const inactive = FACETS.filter((f) => !isChipActive(f.id));

  return (
    <div className={`relative ${className}`}>
      <div className="flex items-center gap-2 overflow-x-auto no-scrollbar px-4 py-2">
        {/* ≡ summary chip */}
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          aria-label={t('resources.filter.title', 'Filters')}
          className="shrink-0 flex items-center gap-1.5 px-2.5 py-2 rounded-full bg-zinc-800 border border-zinc-700 text-zinc-300 active:bg-zinc-700"
        >
          <SlidersHorizontal size={14} />
          {activeFilterCount > 0 && (
            <span className="min-w-[16px] h-4 px-1 rounded-full bg-indigo-500 text-white text-[10px] font-bold inline-flex items-center justify-center">
              {activeFilterCount}
            </span>
          )}
        </button>

        {/* Applied facets */}
        {active.map((f) => {
          const Icon = f.icon;
          return (
            <div
              key={f.id}
              className="shrink-0 flex items-center rounded-full bg-indigo-500 text-white text-sm font-medium overflow-hidden"
            >
              <button
                type="button"
                onClick={(e) => openFacet(f.id, e)}
                className="flex items-center gap-1.5 pl-3 pr-2 py-1.5 active:bg-indigo-600 whitespace-nowrap"
              >
                <Icon size={13} className="opacity-90" />
                {facetActiveLabel(f.id, chipValues, allTags)}
              </button>
              <button
                type="button"
                onClick={() => clearChip(f.id)}
                aria-label={t('resources.filter.clear', 'Clear')}
                className="pr-2.5 pl-1 py-1.5 active:bg-indigo-600"
              >
                <X size={14} className="opacity-90" />
              </button>
            </div>
          );
        })}

        {/* Unapplied facets */}
        {inactive.map((f) => {
          const Icon = f.icon;
          return (
            <button
              key={f.id}
              type="button"
              onClick={(e) => openFacet(f.id, e)}
              className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-zinc-800 border border-zinc-700 text-zinc-300 text-sm active:bg-zinc-700 whitespace-nowrap"
            >
              <Icon size={13} className="text-zinc-400" />
              {f.label}
              <ChevronDown size={13} className="text-zinc-500" />
            </button>
          );
        })}
      </div>

      {/* Bounded-facet dropdown */}
      {dropdown && (
        <FacetDropdown
          facetId={dropdown.id}
          anchorRect={dropdown.rect}
          config={config}
          availablePlatforms={availablePlatforms}
          onClose={() => setDropdown(null)}
        />
      )}

      {/* ≡ menu */}
      {menuOpen && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setMenuOpen(false)} />
          <div className="absolute left-4 top-12 z-50 w-60 rounded-2xl bg-zinc-800/95 backdrop-blur border border-zinc-700 shadow-2xl overflow-hidden">
            <div className="px-4 py-3 text-sm text-zinc-300 border-b border-zinc-700/60">
              {activeFilterCount > 0
                ? t('resources.filter.nApplied', '{{n}} filters applied.', {
                    n: activeFilterCount,
                  })
                : t('resources.filter.noneApplied', 'No filters applied.')}
            </div>
            <button
              type="button"
              disabled={activeFilterCount === 0}
              onClick={() => {
                clearAll();
                setMenuOpen(false);
              }}
              className="w-full text-left px-4 py-3 text-sm font-semibold text-red-400 active:bg-zinc-700 disabled:opacity-40"
            >
              {t('resources.filter.clearAll', 'Clear all filters')}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
