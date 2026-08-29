/**
 * GenFooterControls popovers — two reports from using the real thing.
 *
 * 1. "Sometimes it vanishes when I move up." The gap between pill and popover
 *    was an 8px MARGIN — space outside the container — so travelling from one
 *    to the other fired `mouseleave` and closed the menu mid-reach.
 *
 *    Hover-to-open is deliberate IC parity (see Pill's onHover), so the fix is
 *    not to drop mouseleave but to remove the dead space: `padding` keeps the
 *    same visual offset while the container covers the path the pointer takes.
 *
 * 2. The popover is anchored to the whole pill row (`left-0`), not to the
 *    pill that opened it, so it drifts off to the left. IC puts it directly
 *    above its trigger. Direction stays UP — that is what IC does and what an
 *    earlier revision of this change got backwards.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k) }),
}));

import { GenFooterControls } from './GenFooterControls';
import type { PromptGenSettings } from '../types';

afterEach(cleanup);

const GEN: PromptGenSettings = { kind: 'image', model: '', ratio: '1:1', count: 1 };

function renderFooter() {
  return render(
    <GenFooterControls
      gen={GEN}
      models={[{ name: 'm1', display_name: 'Model One' }]}
      onChange={() => {}}
    />,
  );
}

/** Open the count popover the way a real pointer does.
 *
 *  A real click is ALWAYS preceded by mouseenter. `fireEvent.click` alone is
 *  not — which is why the unit suite never saw that hover-open and
 *  click-toggle were cancelling each other out. Drive both. */
function clickCount() {
  const pill = screen.getByTestId('pill-count');
  fireEvent.mouseEnter(pill);
  fireEvent.click(pill);
}

/** Hover only, no click. */
function hoverCount() {
  fireEvent.mouseEnter(screen.getByTestId('pill-count'));
}

const openCount = clickCount;

/** Is the count popover on screen? Asserted through its options rather than
 *  its title: the visible "COUNT" is CSS uppercase, the DOM says "Count". */
const countOpen = () => screen.queryAllByTestId('count-option').length > 0;

describe('GenFooterControls popovers', () => {
  it('leaves no dead space between the pill and the popover', () => {
    // A margin here is a gap the pointer crosses while OUTSIDE the container,
    // which fires mouseleave and closes the menu before it can be reached.
    // Padding gives the same look with no such gap.
    renderFooter();
    openCount();
    const panel = screen.getAllByTestId('count-option')[0].closest('div[class*="absolute"]');
    expect(panel).not.toBeNull();
    expect(
      panel!.className,
      'offset is a margin — the pointer leaves the container crossing it',
    ).not.toMatch(/\bm[trbl]?-\d/);
    expect(panel!.className, 'no padding to bridge the gap').toMatch(/\bp[trbl]?-\d/);
  });



  it('still closes on a click outside', () => {
    renderFooter();
    openCount();
    expect(countOpen()).toBe(true);

    fireEvent.mouseDown(document.body);
    expect(countOpen()).toBe(false);
  });

  it('still closes when the same pill is clicked again', () => {
    renderFooter();
    clickCount();
    expect(countOpen()).toBe(true);
    clickCount();
    expect(countOpen()).toBe(false);
  });

  it('a real click OPENS it — hover-open must not cancel the click', () => {
    // The pointer enters the pill (hover opens) and then presses. If click is
    // a plain toggle it closes what hover just opened, and the control looks
    // dead to anyone who clicks rather than hovers.
    renderFooter();
    clickCount();
    expect(countOpen(), 'clicking the pill did not open it').toBe(true);
  });

  it('a clicked popover survives the pointer leaving — it was pinned', () => {
    // Hover-opened menus follow the pointer; a menu the user deliberately
    // clicked should stay until dismissed.
    const { container } = renderFooter();
    clickCount();
    fireEvent.mouseLeave(container.firstElementChild as HTMLElement);
    expect(countOpen(), 'clicked popover vanished on mouseleave').toBe(true);
  });

  it('a hover-opened popover still follows the pointer away (IC parity)', () => {
    const { container } = renderFooter();
    hoverCount();
    expect(countOpen()).toBe(true);
    fireEvent.mouseLeave(container.firstElementChild as HTMLElement);
    expect(countOpen(), 'hover-opened popover should close on leave').toBe(false);
  });

  it('opens upward, above the pills — as IC does', () => {
    renderFooter();
    openCount();
    const panel = screen.getAllByTestId('count-option')[0].closest('div[class*="absolute"]');
    expect(panel, 'could not find the positioned popover').not.toBeNull();
    expect(panel!.className, 'popover no longer opens upward').toContain('bottom-full');
  });

  it('is positioned against its trigger, not pinned to the row', () => {
    // jsdom has no layout, so the alignment itself is an e2e assertion; what
    // is checkable here is that the popover carries an explicit offset rather
    // than the `left-0` that stuck it to the whole row.
    renderFooter();
    openCount();
    const panel = screen
      .getAllByTestId('count-option')[0]
      .closest('div[class*="absolute"]') as HTMLElement;
    expect(
      panel.className,
      'still hard-pinned to the row with left-0',
    ).not.toMatch(/\bleft-0\b/);
    expect(panel.style.left, 'no explicit anchor offset was applied').not.toBe('');
  });

  it('only one popover is open at a time', () => {
    renderFooter();
    openCount();
    expect(countOpen()).toBe(true);
    fireEvent.click(screen.getByTestId('pill-quality'));
    expect(countOpen(), 'both popovers open at once').toBe(false);
    expect(screen.getAllByTestId('quality-option').length).toBeGreaterThan(0);
  });
});
