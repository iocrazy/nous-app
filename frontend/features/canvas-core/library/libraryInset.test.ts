/**
 * The Library panel is an OVERLAY, and until this module existed no other
 * canvas island knew it was there. The composer centres itself with
 * `inset-x-0 mx-auto`, the save badge and the Arrange button anchor to
 * `right-4` — all three lay themselves out against the FULL surface, so
 * opening the panel slid them underneath it (user screenshot: the prompts
 * page's "Save current… / New…" footer sitting on top of the composer's
 * Group / Export / Import row).
 *
 * The reservation is derived in ONE place and published as CSS custom
 * properties, so a new island opts in with a class string instead of
 * re-deriving the panel's geometry.
 */

import { describe, expect, it } from 'vitest';

import { ISLAND_GAP, NO_INSET, PANEL_GUTTER, libraryInset } from './libraryInset';

describe('libraryInset', () => {
  it('reserves nothing while the panel is closed', () => {
    expect(libraryInset({ open: false, drawer: false, width: 600 })).toEqual(NO_INSET);
    // Closed-in-drawer-mode is the same answer: what is not on screen takes
    // no room, and returning a bottom reservation here would push the
    // composer up against nothing.
    expect(libraryInset({ open: false, drawer: true, width: 600 })).toEqual(NO_INSET);
  });

  it('reserves the panel width plus its gutter on the right when docked', () => {
    expect(libraryInset({ open: true, drawer: false, width: 600 })).toEqual({
      right: `${600 + PANEL_GUTTER + ISLAND_GAP}px`,
      bottom: '0px',
    });
    // The Media page is narrower than the Prompts page, so the reservation
    // has to track the live width rather than a constant.
    expect(libraryInset({ open: true, drawer: false, width: 340 }).right).toBe(
      `${340 + PANEL_GUTTER + ISLAND_GAP}px`,
    );
  });

  it('reserves the bottom, not the right, once the panel is a drawer', () => {
    // Below the breakpoint the panel spans the full width along the bottom.
    // Keeping a right reservation there would shove the composer sideways
    // for no reason while still leaving it under the drawer.
    const inset = libraryInset({ open: true, drawer: true, width: 600 });
    expect(inset.right).toBe('0px');
    expect(inset.bottom).toContain('45vh');
    expect(inset.bottom).not.toBe('0px');
  });
});
