/**
 * Viewport math for the canvas core (Phase 1 Day 2-7).
 *
 * The canvas viewport is a translate-then-zoom transform; React Flow uses
 * the same convention. screenToWorld is the inverse of the apply step.
 */

import type { CanvasViewport } from '../types';

export interface ScreenPoint {
  x: number;
  y: number;
}

export interface WorldPoint {
  x: number;
  y: number;
}

export const IDENTITY_VIEWPORT: CanvasViewport = Object.freeze({
  x: 0,
  y: 0,
  zoom: 1,
});

/** World → screen.  screen = (world * zoom) + offset.  */
export function worldToScreen(
  world: WorldPoint,
  viewport: CanvasViewport,
): ScreenPoint {
  const zoom = safeZoom(viewport.zoom);
  return {
    x: world.x * zoom + viewport.x,
    y: world.y * zoom + viewport.y,
  };
}

/** Screen → world. Inverse of worldToScreen. */
export function screenToWorld(
  screen: ScreenPoint,
  viewport: CanvasViewport,
): WorldPoint {
  const zoom = safeZoom(viewport.zoom);
  return {
    x: (screen.x - viewport.x) / zoom,
    y: (screen.y - viewport.y) / zoom,
  };
}

/**
 * Zoom around a fixed anchor in screen space — the world point under the
 * anchor stays put after the zoom is applied. Used by wheel-to-zoom and
 * pinch-to-zoom handlers.
 */
export function zoomAroundScreenAnchor(
  viewport: CanvasViewport,
  anchor: ScreenPoint,
  nextZoom: number,
): CanvasViewport {
  const zoomBefore = safeZoom(viewport.zoom);
  const zoomAfter = safeZoom(nextZoom);
  if (zoomBefore === zoomAfter) return viewport;
  // The world point currently under `anchor`:
  const worldAtAnchor = screenToWorld(anchor, viewport);
  return {
    zoom: zoomAfter,
    // Solve: anchor = worldAtAnchor * zoomAfter + offset
    x: anchor.x - worldAtAnchor.x * zoomAfter,
    y: anchor.y - worldAtAnchor.y * zoomAfter,
  };
}

/**
 * Pan the viewport by a screen-space delta. Equivalent to dragging the
 * scene under the mouse.
 */
export function panByScreenDelta(
  viewport: CanvasViewport,
  dx: number,
  dy: number,
): CanvasViewport {
  return { ...viewport, x: viewport.x + dx, y: viewport.y + dy };
}

/**
 * Clamp zoom to a sane range. Outside of [0.05, 8] the math still works
 * but the UX falls apart — 0.05× means a pixel is barely visible, 8×
 * means single nodes fill the screen.
 */
export const MIN_ZOOM = 0.05;
export const MAX_ZOOM = 8;

export function clampZoom(zoom: number): number {
  if (!Number.isFinite(zoom)) return 1;
  return Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom));
}

function safeZoom(zoom: number): number {
  // Treat 0 / NaN / negative as identity to avoid Infinity downstream.
  if (!Number.isFinite(zoom) || zoom <= 0) return 1;
  return zoom;
}

// ============================================================
// Empty-viewport self-heal (2026-08-12 production incident)
// ============================================================

export interface SurfaceSize {
  width: number;
  height: number;
}

export interface WorldRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Fallback node box used when a node carries no size of its own. Nodes are
 * sanitized on load (`stripRfInternals` drops `measured`/`width`/`height`),
 * so on a fresh load this is the size EVERY non-group node reports — the
 * same 200×120 default `CanvasEngine.nodeMeasure` uses for its guide math,
 * kept identical so the two never disagree about where a node "is".
 */
export const DEFAULT_NODE_BOX: SurfaceSize = Object.freeze({
  width: 200,
  height: 120,
});

/** The world-space rectangle a viewport shows on a `size`d surface. */
export function visibleWorldRect(
  viewport: CanvasViewport,
  size: SurfaceSize,
): WorldRect {
  const zoom = safeZoom(viewport.zoom);
  return {
    x: -viewport.x / zoom,
    y: -viewport.y / zoom,
    width: size.width / zoom,
    height: size.height / zoom,
  };
}

/** A node's world-space box: `position` (top-left) + its declared size. */
function nodeWorldRect(node: unknown): WorldRect | null {
  if (!node || typeof node !== 'object') return null;
  const obj = node as Record<string, unknown>;
  const pos = obj.position as { x?: unknown; y?: unknown } | undefined;
  if (!pos || typeof pos.x !== 'number' || typeof pos.y !== 'number') return null;
  if (!Number.isFinite(pos.x) || !Number.isFinite(pos.y)) return null;
  // Groups carry their box in `style` ({width,height} from grouping.ts); a
  // node that survived a save with `measured` (or was just measured in this
  // session) carries it there. Both are optional — see DEFAULT_NODE_BOX.
  const style = (obj.style ?? {}) as Record<string, unknown>;
  const measured = (obj.measured ?? {}) as Record<string, unknown>;
  const width =
    pickPositive(style.width) ??
    pickPositive(measured.width) ??
    pickPositive(obj.width) ??
    DEFAULT_NODE_BOX.width;
  const height =
    pickPositive(style.height) ??
    pickPositive(measured.height) ??
    pickPositive(obj.height) ??
    DEFAULT_NODE_BOX.height;
  return { x: pos.x, y: pos.y, width, height };
}

function pickPositive(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? value
    : null;
}

function rectsOverlap(a: WorldRect, b: WorldRect): boolean {
  return (
    a.x < b.x + b.width &&
    a.x + a.width > b.x &&
    a.y < b.y + b.height &&
    a.y + a.height > b.y
  );
}

/**
 * Does the persisted viewport frame ANY node at all?
 *
 * Criterion, deliberately the most conservative one that still catches the
 * production failure (canvas 337610660408263, 2026-08-12): a node counts as
 * framed when its world box **overlaps the visible world rect by even one
 * pixel**. Only when EVERY node misses the rect entirely do we call the
 * viewport empty — a viewport that shows so much as a node's corner is left
 * exactly as the user saved it. Nodes with no usable `position` are ignored
 * (they render at 0,0 and would otherwise vote "framed" for free).
 *
 * Returns `true` for an empty node list: nothing to frame means nothing is
 * broken, and the caller must not touch a blank canvas's saved viewport.
 */
export function viewportFramesAnyNode(
  viewport: CanvasViewport,
  nodes: readonly unknown[],
  size: SurfaceSize,
): boolean {
  if (nodes.length === 0) return true;
  const view = visibleWorldRect(viewport, size);
  let considered = 0;
  for (const node of nodes) {
    const rect = nodeWorldRect(node);
    if (!rect) continue;
    considered += 1;
    if (rectsOverlap(rect, view)) return true;
  }
  // Every node lacked a position — we know nothing, so claim "framed" and
  // leave the viewport alone rather than fitting on no evidence.
  return considered === 0;
}
