// features/canvas-core/library/libraryInset.ts
//
// How much of the canvas surface the Library panel is standing on, in ONE
// place, published to the islands as CSS custom properties.
//
// The panel floats over the board rather than squeezing it (see
// `LibraryPanel`'s header comment) — that is what makes "drag a picture onto
// a node" one gesture, and it is not up for renegotiation. But floating over
// the BOARD was read as floating over everything, and the canvas chrome is
// not the board: `CanvasComposer` centres itself with `inset-x-0 mx-auto`,
// `SaveBadge` and `ArrangeSelectedButton` anchor to `right-4`. All three lay
// themselves out against the full surface width, so opening the panel slid
// them underneath it — the reported symptom was the Prompts page's
// "Save current… / New…" footer sitting on top of the composer's
// Group / Export / Import row.
//
// A CSS variable rather than props threaded down from `CanvasPage`: the
// consumers are leaves that each need exactly one number, and a variable on
// the surface root reaches them all without giving any of them a reason to
// import the Library's store. A new island opts in with a class string.
//
// ⚠️ The reservation is derived from the SAME two inputs the panel positions
// itself from — `width` and the drawer breakpoint. Anyone changing the
// panel's geometry has to change it here, and `libraryInset.test.ts` plus
// `CanvasPage.libraryInset.test.tsx` are what say so out loud.

import { useEffect, useState } from 'react';

import { useLibraryStore } from './libraryStore';

/** Below this the island becomes a bottom drawer (spec §3.1). */
export const DRAWER_BREAKPOINT = 1100;

/** The panel's own gap from the surface edge — its `left/right/bottom: 14`. */
export const PANEL_GUTTER = 14;

/** Breathing room between the panel and whichever island now stops beside it. */
export const ISLAND_GAP = 8;

/** The drawer's height, kept as the CSS length the panel itself uses so the
 *  reservation tracks it instead of restating it in pixels. */
export const DRAWER_HEIGHT = '45vh';

/** CSS custom property names, exported so consumers and tests spell them once. */
export const INSET_RIGHT_VAR = '--canvas-inset-right';
export const INSET_BOTTOM_VAR = '--canvas-inset-bottom';

export interface LibraryInset {
  /** Surface width the panel occupies on the right, as a CSS length. */
  readonly right: string;
  /** Surface height the panel occupies at the bottom, as a CSS length. */
  readonly bottom: string;
}

/** What every canvas without an open Library reserves: nothing. */
export const NO_INSET: LibraryInset = { right: '0px', bottom: '0px' };

export interface LibraryInsetInput {
  readonly open: boolean;
  readonly drawer: boolean;
  readonly width: number;
}

/**
 * The reservation, as a pure function of the panel's own geometry.
 *
 * Docked and drawer are mutually exclusive on purpose: a drawer spans the
 * full width, so also reserving on the right would shove the composer
 * sideways while still leaving it underneath.
 */
export function libraryInset({ open, drawer, width }: LibraryInsetInput): LibraryInset {
  if (!open) return NO_INSET;
  if (drawer) {
    return { right: '0px', bottom: `calc(${DRAWER_HEIGHT} + ${PANEL_GUTTER + ISLAND_GAP}px)` };
  }
  return { right: `${width + PANEL_GUTTER + ISLAND_GAP}px`, bottom: '0px' };
}

/**
 * Whether the panel is drawn as a bottom drawer.
 *
 * Lives here rather than in `LibraryPanel` so the panel and the reservation
 * cannot disagree about which layout is on screen — a disagreement would
 * reserve the right edge while the drawer covers the bottom, which is the
 * original bug wearing a different hat.
 *
 * A missing `matchMedia` is treated as desktop: guessing "narrow" would hand
 * the drawer layout to every environment that simply does not implement it.
 */
export function useLibraryDrawer(): boolean {
  const [drawer, setDrawer] = useState(false);
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const mq = window.matchMedia(`(max-width: ${DRAWER_BREAKPOINT - 1}px)`);
    const apply = () => setDrawer(mq.matches);
    apply();
    mq.addEventListener?.('change', apply);
    return () => mq.removeEventListener?.('change', apply);
  }, []);
  return drawer;
}

/** The live reservation for the canvas currently on screen. */
export function useLibraryInset(): LibraryInset {
  const open = useLibraryStore((s) => s.open);
  const width = useLibraryStore((s) => s.width);
  const drawer = useLibraryDrawer();
  return libraryInset({ open, drawer, width });
}

/** The reservation as the style object a surface root spreads onto itself. */
export function insetStyle(inset: LibraryInset): React.CSSProperties {
  return {
    [INSET_RIGHT_VAR]: inset.right,
    [INSET_BOTTOM_VAR]: inset.bottom,
  } as React.CSSProperties;
}
