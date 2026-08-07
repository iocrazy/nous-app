// frontend/components/workspace/EpisodeViewTabs.tsx
//
// EpisodeViewTabs — the horizontal segmented control that sits at the top of a
// surface node's content area (B5, T-B5.4). Given a node's creative `surface`
// (nodeSurface.ts::viewsForNode) it renders that surface's "view set" as an
// island-style segmented control:
//
//   Script     → Script | Beats
//   Storyboard → Storyboard | Canvas | Shot List
//   Renders    → Renders
//
// Deliverable-only nodes (surface === null) have an EMPTY view set — the caller
// must not render this control at all (it returns null defensively anyway).
//
// Purely presentational: it owns no state and knows nothing about what each
// view renders. The parent (ProjectWorkspace) holds the active-view state and
// decides what content shows per key. Visual language reuses the island token
// ladder (--island-2 / --line / --accent-*) so it matches the rest of the
// warm-paper shell; no legacy hue literals.
//
// ⚠️ ASSUMPTION (needs the login-gated design mock to confirm): the exact
// placement, size and pill vs. underline treatment of this strip are a best
// guess. It is deliberately a small, self-contained control so the visual can
// be retuned without touching wiring.

import React from 'react';
import { useTranslation } from 'react-i18next';
import type { SurfaceView } from './nodeSurface';

export interface EpisodeViewTabsProps {
  /** View set for the current node (from `viewsForNode`). Empty → renders nothing. */
  views: SurfaceView[];
  /** Key of the active view. */
  active: string;
  /** Fired with the newly-selected view key. */
  onChange: (key: string) => void;
  /** Optional right-aligned slot (actions), mirrors AILibraryTabs' `actions`. */
  actions?: React.ReactNode;
  className?: string;
}

const SEGMENT_BASE =
  'px-3 py-1.5 text-[13px] font-medium rounded-md transition-colors whitespace-nowrap';
const SEGMENT_ACTIVE =
  'bg-[var(--accent-soft)] text-[var(--accent-text)]';
const SEGMENT_IDLE =
  'text-content-2 hover:text-content';

export const EpisodeViewTabs: React.FC<EpisodeViewTabsProps> = ({
  views,
  active,
  onChange,
  actions,
  className,
}) => {
  const { t } = useTranslation();

  // Deliverable-only nodes have no views — never render an empty control shell.
  if (!views || views.length === 0) return null;

  return (
    <div
      className={`flex items-center gap-2 ${className ?? ''}`}
      data-testid="episode-view-tabs"
    >
      <div
        role="tablist"
        aria-label={t('projects.episodeViews.label', 'Views')}
        className="inline-flex items-center gap-1 rounded-lg border border-line bg-island-2 p-1"
      >
        {views.map((view) => {
          const isActive = view.key === active;
          return (
            <button
              key={view.key}
              type="button"
              role="tab"
              aria-selected={isActive}
              data-view={view.key}
              onClick={() => {
                if (!isActive) onChange(view.key);
              }}
              className={`${SEGMENT_BASE} ${isActive ? SEGMENT_ACTIVE : SEGMENT_IDLE}`}
            >
              {t(view.labelKey)}
            </button>
          );
        })}
      </div>
      {actions && <div className="ml-auto">{actions}</div>}
    </div>
  );
};

export default EpisodeViewTabs;
