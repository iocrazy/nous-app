/**
 * LibrarySearchCard — the chat tool card for the agent's `LibrarySearch`
 * call (vector layers spec §4.5, PR 5).
 *
 * The result is the backend tool's structured JSON, rendered as is:
 * one row per hit (time code · title · layer chip · score), then a legs line
 * with the same dot rule as the My Downloads chips, and a warning when the
 * vector leg did not run (a degraded answer otherwise looks identical).
 * Clicking a row opens the resource detail page with `state.searchHit`, which
 * the page already turns into its Search Hit card.
 */

import React from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Search } from 'lucide-react';

import type { ChatToolCall } from '../../types/api';
import type { HitLayer, VectorLegOutcome } from '../../services/searchService';
import { useTeamContext } from '../../contexts/TeamContext';
import { HIT_LAYERS, hitLayerLabel, legDotClass } from '../DownloadsView/SearchLegsChips';

type Translate = (key: string, def: string, opts?: Record<string, unknown>) => string;

interface LibrarySearchShot {
  start_ms?: number | null;
  end_ms?: number | null;
}

/** One hit as `library_search_tool._hit_dict` writes it (ids are strings). */
export interface LibrarySearchHit {
  resource_id: string | null;
  media_id: string;
  title: string | null;
  layer: HitLayer;
  score: number;
  shot: LibrarySearchShot | null;
}

interface LibrarySearchResult {
  query?: string;
  hits?: LibrarySearchHit[];
  legs?: Partial<Record<HitLayer, number>> | null;
  vector_leg?: VectorLegOutcome | null;
  error?: string;
}

function formatTimecode(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function shotStart(hit: LibrarySearchHit): number | undefined {
  const start = hit.shot?.start_ms;
  return typeof start === 'number' ? start : undefined;
}

interface HitRowProps {
  hit: LibrarySearchHit;
  onOpen: (hit: LibrarySearchHit) => void;
  tr: Translate;
}

function HitRow({ hit, onOpen, tr }: HitRowProps): React.ReactElement {
  const start = shotStart(hit);
  return (
    <button
      type="button"
      data-testid="library-search-hit"
      disabled={!hit.resource_id}
      onClick={() => onOpen(hit)}
      title={hit.resource_id ? undefined : tr('chat.librarySearch.notInLibrary', 'Not linked to a library item')}
      className="w-full flex items-center gap-2 px-2 py-1 rounded text-left text-xs hover:bg-island-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
    >
      <span className="w-10 shrink-0 tabular-nums text-content-3">
        {start != null ? formatTimecode(start) : '—'}
      </span>
      <span className="flex-1 min-w-0 truncate text-content">
        {hit.title || tr('chat.librarySearch.untitled', 'Untitled')}
      </span>
      <span className="shrink-0 px-1.5 py-0.5 rounded bg-island-2 text-[10px] text-content-2">
        {hitLayerLabel(hit.layer, tr)}
      </span>
      <span className="w-9 shrink-0 text-right tabular-nums text-content-2">
        {Number(hit.score).toFixed(2)}
      </span>
    </button>
  );
}

interface LegsLineProps {
  legs: Partial<Record<HitLayer, number>>;
  vectorLeg?: VectorLegOutcome | null;
  tr: Translate;
}

function LegsLine({ legs, vectorLeg, tr }: LegsLineProps): React.ReactElement {
  return (
    <div data-testid="library-search-legs" className="flex flex-wrap items-center gap-2 text-[10px] text-content-3">
      {HIT_LAYERS.filter((l) => legs[l] != null).map((l) => (
        <span key={l} className="inline-flex items-center gap-1">
          <span aria-hidden="true" className={`inline-block w-1.5 h-1.5 rounded-full ${legDotClass(l, true, vectorLeg)}`} />
          {`${hitLayerLabel(l, tr)} ${legs[l]}`}
        </span>
      ))}
    </div>
  );
}

export interface LibrarySearchCardProps {
  call: ChatToolCall;
}

export function LibrarySearchCard({ call }: LibrarySearchCardProps): React.ReactElement {
  const { t } = useTranslation();
  const tr = t as unknown as Translate;
  const navigate = useNavigate();
  const { selectedTeamId } = useTeamContext();
  const result = (call.result ?? {}) as LibrarySearchResult;
  const query = result.query ?? (typeof call.args?.query === 'string' ? call.args.query : '');
  const hits = Array.isArray(result.hits) ? result.hits : [];

  const openHit = (hit: LibrarySearchHit) => {
    if (!hit.resource_id) return;
    const teamPath = selectedTeamId ? `/team/${selectedTeamId}` : '';
    const start = shotStart(hit);
    const searchHit = { layer: hit.layer, score: hit.score, ...(start != null ? { startMs: start } : {}) };
    navigate(`${teamPath}/resources/file/${hit.resource_id}`, { state: { searchHit } });
  };

  return (
    <div className="mb-1.5 rounded-lg border border-line bg-island px-2 py-1.5" data-testid="library-search-card">
      <div className="flex items-center gap-1.5 px-1 pb-1 text-xs text-content-2">
        <Search className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
        <span className="font-medium">{tr('chat.librarySearch.title', 'Library Search')}</span>
        {query && <span className="truncate text-content-3">“{query}”</span>}
      </div>
      {typeof result.error === 'string' ? (
        <p className="px-1 text-xs text-danger">{result.error}</p>
      ) : (
        <>
          {hits.length === 0 ? (
            <p className="px-1 text-xs text-content-3">
              {tr('chat.librarySearch.noMatches', 'No matches in your library')}
            </p>
          ) : (
            hits.map((hit, idx) => <HitRow key={`${hit.media_id}-${idx}`} hit={hit} onOpen={openHit} tr={tr} />)
          )}
          <div className="px-1 pt-1 space-y-0.5">
            {result.legs && <LegsLine legs={result.legs} vectorLeg={result.vector_leg} tr={tr} />}
            {result.vector_leg && result.vector_leg !== 'ok' && (
              <p data-testid="library-search-vector-leg" className="text-[10px] text-warn">
                {tr('chat.librarySearch.vectorLegDegraded', 'Keyword matches only · semantic search {{outcome}}', {
                  outcome: result.vector_leg,
                })}
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
