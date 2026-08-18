/**
 * The dropdown panel is allowed to be wider than its trigger.
 *
 * WHAT BROKE: the portal panel was sized `width: <trigger width>`. Option rows
 * carry `truncate`, and a row also has to fit padding and a checkmark that the
 * trigger does not — so the menu had LESS room for text than the closed
 * trigger did, and any label longer than the currently selected one was
 * ellipsised. In the Task Center sort menu (trigger 121px showing
 * "Newest first") that clipped the first option to "Newe…" and "Recently
 * updated" to "Recently up…" — both, in the same open menu.
 *
 * The cost was already on record before this fix: `editor/components/
 * HeadingSelect.tsx` exists as a separate dropdown partly because "the
 * app-wide UiSelect is a DARK menu whose panel width tracks its trigger; the
 * short heading triggers make it clip labels".
 *
 * ⚠️ WHAT THIS TEST CAN AND CANNOT CHECK.
 *
 * jsdom has no layout engine: `getBoundingClientRect()` is all zeroes and
 * `scrollWidth` is 0, so nothing here can observe an actual ellipsis or an
 * actual pixel width. What it pins is the SIZING CONTRACT the panel is given —
 * a floor, a ceiling, and no fixed width — which is the thing that regressed.
 * Real widths were measured in Chromium and recorded in the PR body (the old
 * panel came out 121px and clipped 2 of 4 options; the new one 145px and
 * clipped none). That measurement is not automated: CI runs vitest, not
 * Playwright.
 *
 * Assertions are exact-value and positive, never `not.toBe(...)` — a panel
 * that failed to render at all has empty styles, and a negative assertion
 * would pass on it.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

import { UiSelect } from './primitives';

/** Width of the Task Center sort trigger, measured in a real browser. */
const TRIGGER_WIDTH = 121;
const TRIGGER_LEFT = 100;

const SORT_OPTIONS = [
  { value: 'created_desc', label: 'Newest first' },
  { value: 'created_asc', label: 'Oldest first' },
  { value: 'updated_desc', label: 'Recently updated' },
  { value: 'title_asc', label: 'Title A→Z' },
];

/**
 * Render a select, force the trigger to report the real-world narrow rect, and
 * open the menu. jsdom returns zeroes from `getBoundingClientRect` by default,
 * which would make "menu is at least as wide as the trigger" trivially true.
 */
const openMenu = (props: Record<string, unknown> = {}) => {
  render(
    <UiSelect aria-label="Sort" value="created_desc" onChange={vi.fn()} {...props}>
      {SORT_OPTIONS.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </UiSelect>,
  );

  const trigger = screen.getByRole('button', { name: 'Sort' });
  vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({
    left: TRIGGER_LEFT, top: 50, right: TRIGGER_LEFT + TRIGGER_WIDTH, bottom: 82,
    width: TRIGGER_WIDTH, height: 32, x: TRIGGER_LEFT, y: 50,
    toJSON: () => ({}),
  } as DOMRect);

  fireEvent.click(trigger);
  return screen.getByRole('listbox', { name: 'Sort' });
};

describe('UiSelect menu — sized to its content, not pinned to its trigger', () => {
  it('gives the panel a min/max width and no fixed width', () => {
    const menu = openMenu();

    // The regression, stated exactly: a fixed width is what clipped labels.
    expect(menu.style.width).toBe('');

    // Floor — never narrower than the trigger, so a wide trigger still looks
    // like it owns its menu.
    expect(menu.style.minWidth).toBe(`${TRIGGER_WIDTH}px`);

    // Ceiling — the room left between the trigger's left edge and the right
    // edge of the viewport (jsdom's window is 1024 wide), which is what keeps
    // a long option from running off screen.
    expect(menu.style.maxWidth).toBe(`${window.innerWidth - TRIGGER_LEFT - 8}px`);
  });

  it('never proposes a ceiling below the floor, even against the right edge', () => {
    // A trigger pushed past the right edge would otherwise compute a NEGATIVE
    // ceiling and collapse the panel to nothing.
    render(
      <UiSelect aria-label="Edge" value="a" onChange={vi.fn()}>
        <option value="a">Alpha</option>
        <option value="b">Beta</option>
      </UiSelect>,
    );
    const trigger = screen.getByRole('button', { name: 'Edge' });
    const farLeft = window.innerWidth + 200;
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({
      left: farLeft, top: 50, right: farLeft + TRIGGER_WIDTH, bottom: 82,
      width: TRIGGER_WIDTH, height: 32, x: farLeft, y: 50, toJSON: () => ({}),
    } as DOMRect);
    fireEvent.click(trigger);

    const menu = screen.getByRole('listbox', { name: 'Edge' });
    expect(menu.style.maxWidth).toBe(`${TRIGGER_WIDTH}px`);
    expect(menu.style.minWidth).toBe(`${TRIGGER_WIDTH}px`);
  });

  it('keeps the 240px floor for searchable menus', () => {
    // Rich/searchable menus already had a 240px minimum; the switch from
    // `width` to `min-width` must not quietly drop it.
    const menu = openMenu({ searchable: true });
    expect(menu.style.minWidth).toBe('240px');
    expect(menu.style.width).toBe('');
  });

  it('renders every option label in full in the DOM', () => {
    // Truncation is visual, so jsdom cannot see it — but a "fix" that shortened
    // the labels instead of widening the panel would be caught here.
    const menu = openMenu();
    for (const option of SORT_OPTIONS) {
      expect(menu.textContent).toContain(option.label);
    }
  });
});
