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
