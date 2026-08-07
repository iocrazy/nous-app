/**
 * Surface → view-set mapping for episode-level workflow nodes (B5).
 *
 * Each workflow node carries a `surface` field (B1, mig 402) describing which
 * creative surface it maps to. The top-of-workspace segmented control shows a
 * per-surface "view set" (e.g. Storyboard → Storyboard | Canvas | Shot List).
 * A `null` surface means the node is delivery-only (no creative views) and
 * should render as a dashed-border deliverable (consumed by T-B5.6).
 *
 * Pure, side-effect-free mapping module. Consumers: T-B5.4 (segmented control),
 * T-B5.6 (deliverable badge / dashed border).
 */

/** Creative surface a node maps to. Mirrors `ProjectStageNode.surface` in types.ts. */
export type NodeSurface = 'script' | 'storyboard' | 'renders' | null;

/** A single selectable view within a surface's view set. `labelKey` is an i18n key. */
export interface SurfaceView {
  key: string;
  labelKey: string;
}

/**
 * View sets per non-null surface. Only the 3 creative surfaces have view sets;
 * `null` (deliverable-only) has none — hence keyed by the non-null union only.
 */
export const SURFACE_VIEWS: Record<'script' | 'storyboard' | 'renders', SurfaceView[]> = {
  script: [
    { key: 'script', labelKey: 'projects.episodeViews.script' },
    { key: 'beats', labelKey: 'projects.episodeViews.beats' },
  ],
  storyboard: [
    { key: 'storyboard', labelKey: 'projects.episodeViews.storyboard' },
    { key: 'canvas', labelKey: 'projects.episodeViews.canvas' },
    { key: 'shotlist', labelKey: 'projects.episodeViews.shotlist' },
  ],
  renders: [{ key: 'renders', labelKey: 'projects.episodeViews.renders' }],
};

/**
 * Resolve a node's surface, defaulting missing/null to `null` (deliverable-only).
 * Most conservative degradation per spec §5③.
 */
export function resolveSurface(node: { surface?: NodeSurface } | null | undefined): NodeSurface {
  return node?.surface ?? null;
}

/** True when the node has no creative surface (deliverable-only) → dashed border (T-B5.6). */
export function isDeliverableOnly(node: { surface?: NodeSurface } | null | undefined): boolean {
  return resolveSurface(node) === null;
}

/** View set for a node: the surface's views, or `[]` when deliverable-only. */
export function viewsForNode(node: { surface?: NodeSurface } | null | undefined): SurfaceView[] {
  const surface = resolveSurface(node);
  return surface === null ? [] : SURFACE_VIEWS[surface];
}
