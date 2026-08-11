/**
 * EpisodeSummaryRow — one row of the workspace Overview's episode accordion
 * (IA redesign Task 4, spec `2026-08-10-workspace-ia-redesign`). Surfaces
 * per-episode counts (scenes / shots / renders) and a segmented pipeline-stage
 * indicator; clicking the row toggles its expanded body (workflow strip +
 * node card slot, rendered by the parent `WorkspaceOverview`).
 *
 * Superseded the old B5 T-B5.5 "rollup row that deep-links away" shape
 * (`isCurrent`/`onSelect`) — the row no longer just switches the workspace's
 * current episode, it also expands IN PLACE to show that episode's work.
 * `isOpen`/`onToggle` replace `isCurrent`/`onSelect`; the row itself no longer
 * owns navigation semantics, `WorkspaceOverview` decides what `onToggle`
 * means (Task 1's URL `ep=` via `handleEpisodeChange`).
 *
 * Node-segmented bar (B4, 2026-08-08): `EpisodeProgress.workflow` now carries
 * real per-episode node counts (`nodes_total`/`nodes_done`), so the bar
 * segments by actual workflow nodes when present. The pre-B4 5-stage
 * status-ladder approximation is kept as the fallback for a missing/empty
 * rollup (old backend responses, e2e stubs) — same degrade-don't-throw
 * philosophy as `stageFill`'s unknown-status handling.
 */

import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown } from 'lucide-react';
import type { EpisodeProgress } from '../../types';

/** The server-derived pipeline stages, in order (episode_repository.py
 * `_derive_episode_status`). Index+1 = how many segments are lit. */
export const EPISODE_STAGES = ['planned', 'drafting', 'boarding', 'boarded', 'rendered'] as const;

/** Number of lit segments (1..5) for a status; 0 for an unknown/future value so
 * the bar degrades to "all empty" rather than throwing. */
export function stageFill(status: string): number {
  const idx = (EPISODE_STAGES as readonly string[]).indexOf(status);
  return idx < 0 ? 0 : idx + 1;
}

interface EpisodeSummaryRowProps {
  episode: EpisodeProgress;
  /** 1-based episode number for the EP read-out. */
  epNumber: number;
  /** Whether this row's accordion body is expanded. */
  isOpen: boolean;
  /** Toggle this row: called with the episode id to open it (or switch to it
   * from another open row), or `null` when THIS row is already open and the
   * click means "collapse". */
  onToggle: (episodeId: string | null) => void;
  /** The expanded body content (workflow strip + node card slot) — owned by
   * the parent so this component stays a dumb accordion shell. Only rendered
   * (and only mounted) while `isOpen`. */
  children?: ReactNode;
}

export function EpisodeSummaryRow({ episode, epNumber, isOpen, onToggle, children }: EpisodeSummaryRowProps) {
  const { t } = useTranslation();
  const wf = episode.workflow;
  const nodeDriven = wf != null && wf.nodes_total > 0;
  const total = nodeDriven ? wf.nodes_total : EPISODE_STAGES.length;
  const fill = nodeDriven ? Math.min(wf.nodes_done, wf.nodes_total) : stageFill(episode.status);
  const statusLabel = t(`projects.workspace.episodeStatus.${episode.status}`, episode.status);

  return (
    <div
      className={`rounded-lg border transition-colors ${
        isOpen ? 'border-[var(--accent-border)]' : 'border-line'
      }`}
    >
      <button
        type="button"
        data-testid={`ep-accordion-row-${episode.episode_id}`}
        data-open={isOpen ? 'true' : undefined}
        onClick={() => onToggle(isOpen ? null : episode.episode_id)}
        className={`w-full text-left rounded-lg p-2.5 flex items-center gap-3 transition-colors ${
          isOpen ? 'bg-[var(--accent-soft)]' : 'bg-island hover:border-[var(--accent-border)]'
        }`}
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 min-w-0">
            <span className="font-mono text-[9px] font-bold tracking-[0.12em] text-[var(--accent-text)] shrink-0">
              EP{epNumber}
            </span>
            <span className="text-[13px] font-medium text-ink-100 truncate">{episode.title}</span>
          </div>
          {/* Mono read-out: scenes / shots / renders. */}
          <div className="font-mono text-[10.5px] text-ink-400 mt-0.5">
            {episode.scene_count} SC · SHOTS {episode.shots_done}/{episode.shots_total} · CUTS{' '}
            {episode.renders_count}
          </div>
        </div>

        <div className="shrink-0 flex flex-col items-end gap-1 w-[104px]">
          {/* Segmented bar: real workflow nodes when present, status ladder otherwise. */}
          <div
            className="flex items-center gap-0.5 w-full"
            data-testid={`ws-rollup-stages-${episode.episode_id}`}
            data-fill={fill}
            data-total={total}
            role="img"
            aria-label={statusLabel}
          >
            {Array.from({ length: total }, (_, i) => (
              <span
                key={i}
                className="h-1.5 flex-1 rounded-full"
                style={{ background: i < fill ? 'var(--accent-text)' : 'var(--line-strong)' }}
              />
            ))}
          </div>
          <span className="text-[10px] text-ink-400">{statusLabel}</span>
        </div>

        <ChevronDown
          size={14}
          aria-hidden
          className={`shrink-0 text-ink-400 transition-transform ${isOpen ? 'rotate-180' : ''}`}
        />
      </button>
      {isOpen && (
        <div
          data-testid={`ep-accordion-body-${episode.episode_id}`}
          className="flex flex-col gap-3 px-2.5 pb-2.5"
        >
          {children}
        </div>
      )}
    </div>
  );
}

export default EpisodeSummaryRow;
