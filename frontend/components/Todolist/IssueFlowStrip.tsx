/**
 * IssueFlowStrip — the flow-progress dot strip for a stage-mirror issue
 * (Issues 专题 ②, mockup artifact 3ea09164).
 *
 * One dot per stage in the global catalog: emerald = completed, indigo + glow =
 * current, grey = future — plus an `x/y` position read-out (detail variant
 * prefixes the current stage name: `Storyboard · 3/6`). The whole strip links to
 * the project's workspace. The dots convey the PROJECT's flow position, not the
 * issue's own status.
 *
 * Presentational only: it is handed the catalog + the current stage id and does
 * no fetching (issueFlow.ts owns the data). Renders nothing when the catalog is
 * empty so a not-yet-loaded / non-mirror row occupies no space.
 */

import { Link } from 'react-router-dom';

import type { ProjectStage } from '../../types';
import { computeDots, flowPosition, stageIndex } from './issueFlow';

interface Props {
  catalog: ProjectStage[];
  /** The project's current stage id (falls back to the mirror issue's own stage). */
  currentStageId: string;
  /** Project-workspace link target. */
  to: string;
  variant?: 'row' | 'detail';
}

const DOT_CLASS: Record<'done' | 'current' | 'future', string> = {
  done: 'bg-emerald-500',
  current: 'bg-indigo-500 ring-[3px] ring-indigo-500/25',
  future: 'bg-line-strong',
};

export function IssueFlowStrip({ catalog, currentStageId, to, variant = 'row' }: Props) {
  if (catalog.length === 0) return null;

  const currentIndex = stageIndex(catalog, currentStageId);
  const dots = computeDots(catalog.length, currentIndex);
  const { x, y } = flowPosition(catalog.length, currentIndex);
  const currentName = currentIndex >= 0 ? catalog[currentIndex].name : null;
  const positionText =
    variant === 'detail' && currentName ? `${currentName} · ${x}/${y}` : `${x}/${y}`;

  return (
    <Link
      to={to}
      onClick={(e) => e.stopPropagation()}
      data-testid="issue-flow-strip"
      title="Open project workspace"
      className="inline-flex items-center gap-1.5 shrink-0 hover:opacity-80 transition-opacity"
    >
      <span className="inline-flex items-center gap-[3px]">
        {dots.map((state, i) => (
          <span
            key={catalog[i].id}
            data-testid={`flow-dot-${state}`}
            title={catalog[i].name}
            className={`w-1.5 h-1.5 rounded-full ${DOT_CLASS[state]}`}
          />
        ))}
      </span>
      <span className="text-[11px] text-ink-500 tabular-nums whitespace-nowrap">
        {positionText}
      </span>
    </Link>
  );
}

export default IssueFlowStrip;
