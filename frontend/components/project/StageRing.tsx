/**
 * StageRing — segmented workflow progress ring for project cards (Phase B B1;
 * re-sourced from the SOP `current_stage` to the workflow badge's
 * position/total in G3). One arc segment per node: filled = passed, glowing =
 * current, faint = upcoming. The center shows "index/total". Segment count
 * follows `total`, so adding a node reshapes the ring without a code change.
 * Renders nothing when `stage` is null (No-workflow project, or enrichment
 * degraded).
 */

import React from 'react';
import type { ProjectWorkflowBadge } from '../../types';

interface RingStage {
  name: string;
  /** 1-based position among the ring's segments. */
  index: number;
  total: number;
}

/**
 * Workflow badge -> ring stage. The ring is workflow-driven only (G3 dropped
 * the SOP `current_stage` fallback), so a No-workflow project — no badge, or
 * a badge with no cursor — yields null and renders no ring. Shared by every
 * card surface that shows the ring (ProjectCard grid, ProjectsQueueView rows)
 * so the "what counts as a ringable badge" condition lives in one place.
 */
export function ringStageFromBadge(
  badge: ProjectWorkflowBadge | null | undefined,
): RingStage | null {
  if (!badge || badge.workflow_position == null || badge.workflow_total <= 0) return null;
  return {
    name: badge.current_node_name ?? '',
    index: badge.workflow_position,
    total: badge.workflow_total,
  };
}

interface StageRingProps {
  stage: RingStage | null | undefined;
  /** Ring diameter in px. */
  size?: number;
  /**
   * Archived projects render a muted full circle with a check mark instead
   * of the segmented stage progress (mockup "A · Stage Ring"). Takes
   * precedence over `stage` when true.
   */
  archived?: boolean;
}

const TAU = Math.PI * 2;

/** Arc path for one ring segment (SVG sweep between two angles, 12 o'clock origin). */
function segmentPath(cx: number, cy: number, r: number, from: number, to: number): string {
  const a0 = from * TAU - Math.PI / 2;
  const a1 = to * TAU - Math.PI / 2;
  const x0 = cx + r * Math.cos(a0);
  const y0 = cy + r * Math.sin(a0);
  const x1 = cx + r * Math.cos(a1);
  const y1 = cy + r * Math.sin(a1);
  const largeArc = to - from > 0.5 ? 1 : 0;
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${largeArc} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

export const StageRing: React.FC<StageRingProps> = ({ stage, size = 44, archived = false }) => {
  if (archived) {
    const c = size / 2;
    const r = c - 4;
    return (
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label="Archived"
        data-testid="stage-ring-archived"
        className="shrink-0"
      >
        <circle cx={c} cy={c} r={r} fill="none" stroke="#55555e" strokeWidth={4} opacity={0.35} />
        <text
          x={c}
          y={c + 4}
          textAnchor="middle"
          className="fill-ink-400 font-semibold"
          fontSize={14}
        >
          ✓
        </text>
      </svg>
    );
  }

  if (!stage || stage.total <= 0 || stage.index <= 0) return null;

  const { index, total } = stage;
  const c = size / 2;
  const r = c - 4;
  // Gap between segments, as a fraction of the full circle.
  const gap = 0.02;
  const span = 1 / total;

  const segments = Array.from({ length: total }, (_, i) => {
    const from = i * span + gap / 2;
    const to = (i + 1) * span - gap / 2;
    const isPast = i < index - 1;
    const isCurrent = i === index - 1;
    return { key: i, d: segmentPath(c, c, r, from, to), isPast, isCurrent };
  });

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={`Stage ${index} of ${total}: ${stage.name}`}
      className="shrink-0"
    >
      <g fill="none" strokeWidth={4} strokeLinecap="round">
        {segments.map((s) => (
          <path
            key={s.key}
            d={s.d}
            className={
              s.isCurrent
                ? 'stroke-indigo-500 drop-shadow-[0_0_3px_rgba(99,102,241,0.9)]'
                : s.isPast
                  ? 'stroke-indigo-500'
                  : 'stroke-ink-600 opacity-40'
            }
          />
        ))}
      </g>
      <text
        x={c}
        y={c + 4}
        textAnchor="middle"
        className="fill-ink-100 font-semibold"
        fontSize={11}
      >
        {index}/{total}
      </text>
    </svg>
  );
};

export default StageRing;
