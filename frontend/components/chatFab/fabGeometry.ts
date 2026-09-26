/**
 * Geometry of the collapsed chat entry (the FAB). Pure functions so the
 * clamping / snapping / click-vs-drag rules are testable without a DOM.
 *
 * Coordinates are viewport px, top-left origin. The FAB is positioned by
 * `left` + `top`; when docked its `left` is derived from the side.
 */

export type FabSide = 'left' | 'right';

/** Hit area of the button. The mascot art may overflow it a little. */
export const FAB_SIZE_PX = 56;
/** Distance from the docked edge and from the bottom of the viewport. */
export const FAB_GUTTER_PX = 8;
/** Below this displacement a press-and-release is a click, not a drag. */
export const CLICK_PX = 4;
/**
 * The app's TopBar is 48px tall (h-12) at z-50, above the widget. Nothing
 * of the FAB may sit in that band or it becomes unreachable. Shared with
 * the expanded window's clamp in FloatingChatWidget.
 */
export const TOP_CHROME_PX = 56; // 48px TopBar + 8px breathing room
/** Where the FAB rested before it became draggable: Tailwind `bottom-20`. */
const LEGACY_BOTTOM_PX = 80;

export function clampFabTop(top: number, viewportHeight: number): number {
  const max = viewportHeight - FAB_SIZE_PX - FAB_GUTTER_PX;
  return Math.max(TOP_CHROME_PX, Math.min(max, top));
}

export function clampFabLeft(left: number, viewportWidth: number): number {
  const max = viewportWidth - FAB_SIZE_PX - FAB_GUTTER_PX;
  return Math.max(FAB_GUTTER_PX, Math.min(max, left));
}

export function defaultFabTop(viewportHeight: number): number {
  return clampFabTop(viewportHeight - FAB_SIZE_PX - LEGACY_BOTTOM_PX, viewportHeight);
}

/** Which edge a released FAB snaps to, from the x of its centre. */
export function snapSide(centerX: number, viewportWidth: number): FabSide {
  return centerX < viewportWidth / 2 ? 'left' : 'right';
}

export function dockedLeft(side: FabSide, viewportWidth: number): number {
  return side === 'left' ? FAB_GUTTER_PX : viewportWidth - FAB_SIZE_PX - FAB_GUTTER_PX;
}

/** True while the pointer has not travelled far enough to count as a drag. */
export function isClickGesture(dx: number, dy: number): boolean {
  return Math.hypot(dx, dy) < CLICK_PX;
}
