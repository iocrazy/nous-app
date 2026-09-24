// frontend/components/DownloadsView/SearchLegsChips.tsx
//
// The chip-row increment a hybrid search adds to My Downloads:
//   Layer · All ⌄   Sort · Similarity ⌄   ● Text 12 ● Semantic 9 ○ Visual …   21 hits · …
//
// Rendered into FilterBar's `trailing` slot so it shares the existing chip
// row. Renders nothing when the response carries no `legs` — a backend that
// predates vector spaces must leave no trace in the toolbar.

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowUpDown, Layers } from 'lucide-react';

import { FilterChip } from '../resources/filter/FilterChip';
import type { HitLayer, VectorLegOutcome } from '../../services/searchService';

export type HitLayerFilter = HitLayer | 'all';
export type HitSort = 'similarity' | 'date';

/** Fixed display order of the retrieval layers. */
export const HIT_LAYERS: readonly HitLayer[] = ['text', 'semantic', 'visual', 'camera', 'transcript'];

/** Layers whose index is built by shot indexing, not by this PR's backend. */
const SHOT_LAYERS: ReadonlySet<HitLayer> = new Set(['visual', 'camera']);

export interface SearchLegsChipsProps {
  legs?: Partial<Record<HitLayer, number>> | null;
  vectorLeg?: VectorLegOutcome | null;
  reranked?: boolean;
  layer: HitLayerFilter;
  onLayerChange: (layer: HitLayerFilter) => void;
  sort: HitSort;
  onSortChange: (sort: HitSort) => void;
  /** Hits currently shown (after the Layer filter). */
  hits: number;
  processingMs?: number;
}

type Translate = (key: string, def: string, opts?: Record<string, unknown>) => string;

export function hitLayerLabel(layer: HitLayer, t: Translate): string {
  switch (layer) {
    case 'text':
      return t('library.vectorSearch.legs.text', 'Text');
    case 'semantic':
      return t('library.vectorSearch.legs.semantic', 'Semantic');
    case 'visual':
      return t('library.vectorSearch.legs.visual', 'Visual');
    case 'camera':
      return t('library.vectorSearch.legs.camera', 'Camera');
    case 'transcript':
      return t('library.vectorSearch.legs.transcript', 'Transcript');
  }
}

/**
 * Dot colour for one leg. The semantic leg is judged by the vector-leg
 * OUTCOME, not by its row count: when the engine is down the text leg still
 * fills the page and the response is otherwise identical, so the outcome is
 * the only honest signal.
 */
export function legDotClass(
  layer: HitLayer,
  built: boolean,
  vectorLeg?: VectorLegOutcome | null,
): string {
  if (layer === 'semantic' && vectorLeg) {
    if (vectorLeg === 'ok') return 'bg-ok';
    if (vectorLeg.startsWith('skipped_')) return 'bg-warn';
    return 'bg-danger';
  }
  return built ? 'bg-ok' : 'bg-line-strong';
}

function dotTitle(
  layer: HitLayer,
  built: boolean,
  vectorLeg: VectorLegOutcome | null | undefined,
  t: Translate,
): string {
  if (layer === 'semantic' && vectorLeg) return vectorLeg;
  if (built) return hitLayerLabel(layer, t);
  const notBuilt = t('library.vectorSearch.notBuilt', 'Not built');
  if (SHOT_LAYERS.has(layer)) {
    return `${notBuilt} · ${t('library.vectorSearch.arrivesWithPr3', 'Arrives with PR 3')}`;
  }
  return notBuilt;
}

interface OptionListProps<T extends string> {
  options: ReadonlyArray<{ id: T; label: string }>;
  value: T;
  onPick: (id: T) => void;
}

function OptionList<T extends string>({ options, value, onPick }: OptionListProps<T>) {
  return (
    <div role="menu" className="py-1 min-w-[8rem]">
      {options.map((opt) => (
        <button
          key={opt.id}
          type="button"
          role="menuitemradio"
          aria-checked={opt.id === value}
          onClick={() => onPick(opt.id)}
          className={`block w-full text-left px-3 py-1.5 text-xs transition-colors hover:bg-island-2 ${
            opt.id === value ? 'text-[var(--accent-text)] font-medium' : 'text-content-2'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

export const SearchLegsChips: React.FC<SearchLegsChipsProps> = ({
  legs,
  vectorLeg,
  reranked,
  layer,
  onLayerChange,
  sort,
  onSortChange,
  hits,
  processingMs,
}) => {
  const { t } = useTranslation();
  const tr = t as unknown as Translate;
  const [open, setOpen] = useState<'layer' | 'sort' | null>(null);

  if (!legs) return null;

  const allLabel = tr('library.vectorSearch.layerAll', 'All');
  const layerOptions: Array<{ id: HitLayerFilter; label: string }> = [
    { id: 'all', label: allLabel },
    ...HIT_LAYERS.map((l) => ({ id: l as HitLayerFilter, label: hitLayerLabel(l, tr) })),
  ];
  const sortOptions: Array<{ id: HitSort; label: string }> = [
    { id: 'similarity', label: tr('library.vectorSearch.sortSimilarity', 'Similarity') },
    { id: 'date', label: tr('library.vectorSearch.sortDate', 'Date') },
  ];
  const layerSummary = layerOptions.find((o) => o.id === layer)?.label ?? allLabel;
  const sortSummary = sortOptions.find((o) => o.id === sort)?.label ?? sortOptions[0].label;

  const hitsLine =
    processingMs != null
      ? tr('library.vectorSearch.hitsLine', '{{count}} hits · best match per video · {{ms}} ms', {
          count: hits,
          ms: Math.round(processingMs),
        })
      : tr('library.vectorSearch.hitsLineNoTime', '{{count}} hits · best match per video', {
          count: hits,
        });

  return (
    <div className="flex items-center gap-1.5 flex-wrap" data-testid="search-legs-chips">
      <FilterChip
        chipId="hit-layer"
        label={`${tr('library.vectorSearch.layer', 'Layer')} · ${layerSummary}`}
        isActive={layer !== 'all'}
        isOpen={open === 'layer'}
        onToggle={() => setOpen((prev) => (prev === 'layer' ? null : 'layer'))}
        onClose={() => setOpen((prev) => (prev === 'layer' ? null : prev))}
        icon={Layers}
      >
        <OptionList
          options={layerOptions}
          value={layer}
          onPick={(id) => {
            onLayerChange(id);
            setOpen(null);
          }}
        />
      </FilterChip>

      <FilterChip
        chipId="hit-sort"
        label={`${tr('library.vectorSearch.sort', 'Sort')} · ${sortSummary}`}
        isActive={sort !== 'similarity'}
        isOpen={open === 'sort'}
        onToggle={() => setOpen((prev) => (prev === 'sort' ? null : 'sort'))}
        onClose={() => setOpen((prev) => (prev === 'sort' ? null : prev))}
        icon={ArrowUpDown}
      >
        <OptionList
          options={sortOptions}
          value={sort}
          onPick={(id) => {
            onSortChange(id);
            setOpen(null);
          }}
        />
      </FilterChip>

      <div className="inline-flex items-center gap-2.5 px-2.5 py-1 rounded-lg border border-line bg-island-2 text-xs text-content-2">
        {HIT_LAYERS.map((l) => {
          const count = legs[l];
          const built = count != null;
          return (
            <span
              key={l}
              className="inline-flex items-center gap-1 whitespace-nowrap"
              title={dotTitle(l, built, vectorLeg, tr)}
            >
              <span
                data-testid={`leg-dot-${l}`}
                aria-hidden="true"
                className={`inline-block w-1.5 h-1.5 rounded-full ${legDotClass(l, built, vectorLeg)}`}
              />
              <span className={built ? 'text-content' : 'text-content-3'}>
                {built ? `${hitLayerLabel(l, tr)} ${count}` : hitLayerLabel(l, tr)}
              </span>
            </span>
          );
        })}
        <span className="text-content-3 whitespace-nowrap">
          ·{' '}
          {reranked
            ? tr('library.vectorSearch.rerankOn', 'Rerank On')
            : tr('library.vectorSearch.rerankOff', 'Rerank Off')}
        </span>
      </div>

      <span className="text-xs text-content-3 whitespace-nowrap">{hitsLine}</span>
    </div>
  );
};
