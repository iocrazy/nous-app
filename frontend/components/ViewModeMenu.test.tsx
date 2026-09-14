/**
 * ViewModeMenu — one button that says which view you are in and offers the
 * others.
 *
 * It replaces two different controls that had the same problem in different
 * shapes:
 *
 *  * My Downloads rendered FOUR buttons in a row (`DownloadsView`), with no
 *    grouping role and no `aria-pressed` — four unlabelled glyphs where three
 *    of them are always the wrong answer.
 *  * My Uploads rendered ONE button that CYCLED grid → justified → list
 *    (`ResourceGrid`). Not a broken dropdown, a deliberate cycle — but a
 *    control that neither names your current mode nor admits how many there
 *    are, so the only way to find a view is to keep clicking past it.
 *
 * The mode LIST is a prop because the two surfaces genuinely differ: Downloads
 * has a `feed` view that Uploads does not, and their preferences live under
 * separate storage keys. Hard-coding one list here would have quietly grown a
 * feed option on a page with no feed renderer.
 *
 * `role="menu"` promises a keyboard contract, so these tests spend most of
 * their weight there — declaring the role and not honouring it strands a
 * screen-reader user in a widget that just told them how it behaves.
 */
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { LayoutGrid, LayoutList, LayoutTemplate } from 'lucide-react';

// An INTERPOLATING mock: the trigger's aria-label is
// `t('…', { mode, defaultValue: 'View: {{mode}}' })`, and a passthrough mock
// hands back the options object, so the label reads `[object Object]` and the
// assertion below fails for a reason that has nothing to do with the subject.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: string | Record<string, unknown>) => {
      const text = typeof d === 'string' ? d : ((d?.defaultValue as string) ?? k);
      if (typeof d !== 'object' || d === null) return text;
      return text.replace(/\{\{(\w+)\}\}/g, (m, name: string) =>
        name in d ? String(d[name]) : m,
      );
    },
  }),
}));

import { ViewModeMenu } from './ViewModeMenu';

const MODES = [
  { value: 'justified', icon: LayoutTemplate, label: 'Justified View' },
  { value: 'grid', icon: LayoutGrid, label: 'Grid View' },
  { value: 'list', icon: LayoutList, label: 'List View' },
];

function mount(value = 'grid', onChange = vi.fn()) {
  render(<ViewModeMenu modes={MODES} value={value} onChange={onChange} />);
  return onChange;
}

const trigger = () => screen.getByTestId('view-mode-trigger');
const items = () => screen.getAllByRole('menuitemradio');

describe('ViewModeMenu — what it says before you open it', () => {
  it('names the mode you are in, so the button is not a mystery glyph', () => {
    mount('justified');
    expect(trigger().getAttribute('aria-label')).toContain('Justified View');
  });

  it('shows the current mode\'s own icon', () => {
    const { container } = render(
      <ViewModeMenu modes={MODES} value="list" onChange={() => {}} />,
    );
    expect(container.querySelector('svg.lucide-layout-list')).toBeTruthy();
    expect(container.querySelector('svg.lucide-layout-grid')).toBeNull();
  });

  it('advertises that there is a menu behind it', () => {
    mount();
    expect(trigger().getAttribute('aria-haspopup')).toBe('menu');
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
  });

  it('stays closed until asked', () => {
    mount();
    expect(screen.queryByRole('menu')).toBeNull();
  });
});

describe('ViewModeMenu — choosing', () => {
  it('offers every mode, marking the current one', () => {
    mount('grid');
    fireEvent.click(trigger());
    expect(items()).toHaveLength(3);
    const checked = items().filter((i) => i.getAttribute('aria-checked') === 'true');
    expect(checked).toHaveLength(1);
    expect(within(checked[0]).getByText('Grid View')).toBeTruthy();
  });

  it('reports the chosen mode and closes', () => {
    const onChange = mount('grid');
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole('menuitemradio', { name: /List View/ }));
    expect(onChange).toHaveBeenCalledWith('list');
    expect(screen.queryByRole('menu')).toBeNull();
  });

  // The cycle button it replaces had no way to say "I meant the one I'm on".
  it('re-choosing the current mode closes without a redundant change', () => {
    const onChange = mount('grid');
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Grid View/ }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('an outside click closes it without choosing anything', () => {
    const onChange = mount();
    fireEvent.click(trigger());
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole('menu')).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('ViewModeMenu — the keyboard contract role="menu" promises', () => {
  it('opens on ArrowDown from the trigger and focuses the first item', () => {
    mount();
    fireEvent.keyDown(trigger(), { key: 'ArrowDown' });
    expect(screen.getByRole('menu')).toBeTruthy();
    expect(document.activeElement).toBe(items()[0]);
  });

  it('opening with the mouse also moves focus into the menu', () => {
    mount();
    fireEvent.click(trigger());
    expect(document.activeElement).toBe(items()[0]);
  });

  it('ArrowDown and ArrowUp wrap around the ends', () => {
    mount();
    fireEvent.click(trigger());
    const list = items();
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowUp' });
    expect(document.activeElement).toBe(list[list.length - 1]);
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowDown' });
    expect(document.activeElement).toBe(list[0]);
  });

  it('Home and End jump to the ends', () => {
    mount();
    fireEvent.click(trigger());
    const list = items();
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'End' });
    expect(document.activeElement).toBe(list[list.length - 1]);
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Home' });
    expect(document.activeElement).toBe(list[0]);
  });

  // Without the restore, closing drops the caret at the top of the document
  // and the user tabs back through the whole toolbar to get here again.
  it('Escape closes AND puts focus back on the trigger', () => {
    mount();
    fireEvent.click(trigger());
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it('Tab closes it but leaves focus where the user aimed it', () => {
    mount();
    fireEvent.click(trigger());
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Tab' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).not.toBe(trigger());
  });
});

describe('ViewModeMenu — the mode list is the caller\'s', () => {
  // Downloads has a feed view; Uploads does not. A hard-coded list here would
  // grow a feed option on a page with no feed renderer.
  it('renders exactly the modes it was given', () => {
    render(
      <ViewModeMenu
        modes={[...MODES, { value: 'feed', icon: LayoutList, label: 'Feed View' }]}
        value="feed"
        onChange={() => {}}
      />,
    );
    fireEvent.click(trigger());
    expect(items()).toHaveLength(4);
    expect(screen.getByRole('menuitemradio', { name: /Feed View/ })).toBeTruthy();
  });

  // A stored preference can name a mode this surface does not offer (the two
  // pages keep separate storage keys and separate mode sets).
  it('still opens when the current value is not in the list', () => {
    render(<ViewModeMenu modes={MODES} value="feed" onChange={() => {}} />);
    fireEvent.click(trigger());
    expect(items()).toHaveLength(3);
    expect(items().some((i) => i.getAttribute('aria-checked') === 'true')).toBe(false);
  });
});
