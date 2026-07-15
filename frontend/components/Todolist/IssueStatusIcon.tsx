/**
 * Issue status & priority icons — progress-pie glyphs that encode workflow
 * position (backlog → done), replacing the earlier generic lucide circles
 * where in_progress and in_review were indistinguishable.
 *
 * All labels/orders/colors live in ./issueConfig (single source of truth);
 * this module re-exports them so existing imports keep working and owns only
 * the SVG rendering.
 */

import React from 'react';
import type { IssueStatus, IssuePriority } from '../../services/issuesService';
import { STATUS_CONFIG, PRIORITY_CONFIG, type StatusGlyph } from './issueConfig';

export {
  STATUS_ORDER, STATUS_LABEL, STATUS_COLOR, STATUS_CONFIG,
  PRIORITY_LABEL, PRIORITY_ORDER, PRIORITY_CONFIG,
} from './issueConfig';

/** Draw the pie glyph for a status. Uses currentColor so the wrapping
 *  className (status iconColor) tints it; the done tick punches through with
 *  the island background token for contrast on the filled disc. */
function Glyph({ glyph, size }: { glyph: StatusGlyph; size: number }) {
  const common = { width: size, height: size, viewBox: '0 0 16 16' as const };
  switch (glyph) {
    case 'dashed':
      return (
        <svg {...common} fill="none" stroke="currentColor" strokeWidth={1.7}>
          <circle cx="8" cy="8" r="5.6" strokeDasharray="2.4 2.6" />
        </svg>
      );
    case 'circle':
      return (
        <svg {...common} fill="none" stroke="currentColor" strokeWidth={1.7}>
          <circle cx="8" cy="8" r="5.6" />
        </svg>
      );
    case 'half':
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="5.6" fill="none" stroke="currentColor" strokeWidth={1.7} />
          <path d="M8 4.6a3.4 3.4 0 0 1 0 6.8z" fill="currentColor" />
        </svg>
      );
    case 'threeQuarter':
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="5.6" fill="none" stroke="currentColor" strokeWidth={1.7} />
          <path d="M8 4.6a3.4 3.4 0 1 1-3.4 3.4H8z" fill="currentColor" />
        </svg>
      );
    case 'dot':
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="5.6" fill="none" stroke="currentColor" strokeWidth={1.7} />
          <circle cx="8" cy="8" r="2" fill="currentColor" />
        </svg>
      );
    case 'alert':
      return (
        <svg {...common} fill="none" stroke="currentColor" strokeWidth={1.7}>
          <circle cx="8" cy="8" r="5.6" />
          <path d="M8 5.2v3.2M8 10.8h.01" strokeLinecap="round" />
        </svg>
      );
    case 'check':
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="6.2" fill="currentColor" />
          <path d="m5.4 8.2 1.8 1.8 3.4-3.8" fill="none" stroke="var(--island)" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      );
    case 'cancel':
      return (
        <svg {...common} fill="none" stroke="currentColor" strokeWidth={1.7}>
          <circle cx="8" cy="8" r="5.6" />
          <path d="M5.4 8h5.2" strokeLinecap="round" />
        </svg>
      );
  }
}

export const IssueStatusIcon: React.FC<{ status: IssueStatus; size?: number }> = ({ status, size = 14 }) => {
  const meta = STATUS_CONFIG[status];
  return (
    <span className={meta.iconColor} style={{ display: 'inline-flex' }}>
      <Glyph glyph={meta.glyph} size={size} />
    </span>
  );
};

/** Priority signal — three ascending bars, or an urgent badge for critical.
 *  bars 1–3 light the low→high columns; bars 4 (critical) renders the badge. */
export const PriorityIcon: React.FC<{ priority: IssuePriority; size?: number }> = ({ priority, size = 13 }) => {
  const { bars, color, label } = PRIORITY_CONFIG[priority];
  if (bars >= 4) {
    return (
      <svg width={size} height={size} viewBox="0 0 12 12" className={color} aria-label={label}>
        <rect x="1" y="1" width="10" height="10" rx="2.5" fill="currentColor" />
        <path d="M6 3.4v3.4M6 8.9h.01" stroke="var(--island)" strokeWidth={1.5} strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" fill="currentColor" className={color} aria-label={label}>
      <rect x="1" y="7" width="2.4" height="4" rx="0.7" />
      <rect x="4.8" y="4.5" width="2.4" height="6.5" rx="0.7" opacity={bars >= 2 ? 1 : 0.28} />
      <rect x="8.6" y="2" width="2.4" height="9" rx="0.7" opacity={bars >= 3 ? 1 : 0.28} />
    </svg>
  );
};
