/**
 * EpisodeSummaryRow — one row of the workspace overview's multi-episode rollup
 * (spec §6, T-B5.5). Surfaces per-episode counts (scenes / shots / renders) and
 * a segmented pipeline-stage indicator, and deep-links into the episode when
 * clicked.
 *
 * Node-segmented bar (B4, 2026-08-08): `EpisodeProgress.workflow` now carries
 * real per-episode node counts (`nodes_total`/`nodes_done`), so the bar
 * segments by actual workflow nodes when present. The pre-B4 5-stage
 * status-ladder approximation is kept as the fallback for a missing/empty
 * rollup (old backend responses, e2e stubs) — same degrade-don't-throw
 * philosophy as `stageFill`'s unknown-status handling.
 */

import { useTranslation } from 'react-i18next';
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
  /** Whether this row is the currently-open episode (accent highlight). */
  isCurrent: boolean;
  /** Deep-link into this episode (switches the workspace's current episode). */
  onSelect?: (episodeId: string) => void;
}

export function EpisodeSummaryRow({ episode, epNumber, isCurrent, onSelect }: EpisodeSummaryRowProps) {
  const { t } = useTranslation();
  const wf = episode.workflow;
  const nodeDriven = wf != null && wf.nodes_total > 0;
  const total = nodeDriven ? wf.nodes_total : EPISODE_STAGES.length;
  const fill = nodeDriven ? Math.min(wf.nodes_done, wf.nodes_total) : stageFill(episode.status);
  const statusLabel = t(`projects.workspace.episodeStatus.${episode.status}`, episode.status);
  const clickable = Boolean(onSelect);

  return (
    <button
      type="button"
      data-testid={`ws-rollup-row-${episode.episode_id}`}
      data-current={isCurrent ? 'true' : undefined}
      disabled={!clickable}
      onClick={clickable ? () => onSelect!(episode.episode_id) : undefined}
      className={`w-full text-left rounded-lg border p-2.5 flex items-center gap-3 transition-colors ${
        isCurrent ? 'border-[var(--accent-border)] bg-[var(--accent-soft)]' : 'border-line bg-island'
      } ${clickable ? 'hover:border-[var(--accent-border)] cursor-pointer' : 'cursor-default'}`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-[9px] font-bold tracking-[0.12em] text-[var(--accent-text)] shrink-0">
            EP{epNumber}
          </span>
          <span className="text-[13px] font-medium text-ink-100 truncate">{episode.title}</span>
        </div>
        {/* Mono read-out mirrors the Continue card: scenes / shots / renders. */}
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
    </button>
  );
}

export default EpisodeSummaryRow;
