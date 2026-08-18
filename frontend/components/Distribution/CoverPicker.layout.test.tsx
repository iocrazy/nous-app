/**
 * The cover pair renders as two side-by-side cells, tops flush, each captioned
 * underneath — the shape the platform's own uploader uses.
 *
 * WHAT BROKE: `.cover-slot.h` carried `align-self: center` inside the
 * `.cover-slots` flex row. The two crops are deliberately different heights
 * (3:4 is 120px, 4:3 is 96px), so centring the shorter one against the taller
 * one dropped its top edge by exactly (120 - 96) / 2 = 12px and the pair
 * rendered staggered rather than side by side.
 *
 * ⚠️ WHAT THIS TEST CAN AND CANNOT CHECK — read before trusting it.
 *
 * jsdom resolves the CSS **cascade** (`getComputedStyle` returns the winning
 * declaration, and on the broken stylesheet it really does return
 * `align-self: "center"` here). jsdom does NOT do **layout**: there is no box
 * model, `getBoundingClientRect()` is all zeroes and `scrollWidth` is 0. So
 * this file asserts the *style rules* that produce the alignment, plus the DOM
 * shape they apply to. It does not and cannot measure pixel positions.
 *
 * That is a real guard rather than a restatement of the CSS, because
 * "nowrap flex row + every item resolving a top-aligning cross-axis value ⇒
 * the items share a top edge" is a theorem of the flex spec, not a hope — but
 * it only holds if the cells really are direct children of the row, which is
 * asserted here too. Actual pixel geometry was measured in a real browser and
 * recorded in the PR body; it is not automated (CI runs vitest, not Playwright).
 *
 * ⚠️ EVERY ASSERTION IS POSITIVE (`toBe('flex-start')`, never
 * `not.toBe('center')`). If jsdom ever fails to parse the stylesheet it returns
 * `''` for every property — under a negative assertion that passes vacuously,
 * which is exactly the shape of a test that guards nothing. Positive
 * assertions fail closed. Do not relax them.
 */
import { render } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import React from 'react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import enJson from '../../public/locales/en.json';

vi.mock('../../services/distributionService', () => ({
  extractCoverFrames: vi.fn(),
  selectCoverFrame: vi.fn(),
}));

vi.mock('../../services/resourceService', () => ({
  getResourceFileUrl: (id: string) => `/file/${id}`,
}));

// No client → the component's session effect returns early and no Realtime
// channel is opened. The cover pair renders purely from the `value` prop.
vi.mock('../../supabaseClient', () => ({ getSupabaseClient: () => null }));

import { CoverPicker } from './CoverPicker';

/**
 * The real shipped stylesheet, not a paraphrase of it.
 *
 * Read off disk rather than `import './distribution-v4.css'` or Vite's `?raw`:
 * vitest runs with `css: false`, so BOTH of those hand back an empty string
 * and every assertion below would read the browser default — green-looking
 * output proving nothing. A bad path throws here instead, and
 * `the stylesheet under test is actually loaded` re-checks it at run time.
 */
const CSS = readFileSync(
  resolve(process.cwd(), 'components/Distribution/distribution-v4.css'),
  'utf8',
);

beforeAll(() => {
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);
});

const makeI18n = (): I18n => {
  const inst = createInstance();
  void inst.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
  });
  return inst;
};

/**
 * `.dist-v4` is the page-level wrapper every rule in this stylesheet is scoped
 * under (PublishPage.tsx renders it). Without it the cascade would not apply
 * and every assertion below would read `''` — i.e. the test would go red, not
 * quietly pass.
 */
const renderPicker = (value: { vertical: string; horizontal: string } | null) =>
  render(
    <I18nextProvider i18n={makeI18n()}>
      <div className="dist-v4">
        <CoverPicker
          sources={[{ id: '1', filename: 'launch-cut.mp4' }]}
          value={value}
          onChange={vi.fn()}
        />
      </div>
    </I18nextProvider>,
  );

const styleOf = (el: Element) => window.getComputedStyle(el);

/** Cross-axis values that put a flex item's top edge on the line's top edge. */
const TOP_ALIGNING = ['auto', 'flex-start', 'start', 'stretch'];

const readRow = (container: HTMLElement) => {
  const row = container.querySelector('.cover-slots');
  if (!row) throw new Error('no .cover-slots row rendered');
  return row;
};

describe('cover pair — side by side, tops flush, captioned underneath', () => {
  // Self-check. Every other assertion in this file reads a computed style, and
  // a computed style is indistinguishable from a browser default when the
  // stylesheet failed to load — the exact shape of a test that guards nothing.
  // This one fails loudly in that case. (It has already earned its keep: the
  // first version of this file loaded the CSS through Vite's `?raw`, which
  // returns '' under vitest's `css: false`.)
  it('has the stylesheet under test actually loaded', () => {
    expect(CSS.length).toBeGreaterThan(1000);
    const { container } = renderPicker(null);
    const row = readRow(container);
    // A property no browser default would produce for a plain <div>.
    expect(styleOf(row).display).toBe('flex');
  });

  it('lays the two cells out as one nowrap flex row that top-aligns its items', () => {
    const { container } = renderPicker({ vertical: '10', horizontal: '11' });
    const row = readRow(container);

    // The row itself. `nowrap` is the "不换行" half; `flex-start` is the half
    // that used to be defeated by `align-self: center` on the shorter box.
    expect(styleOf(row).display).toBe('flex');
    expect(styleOf(row).flexWrap).toBe('nowrap');
    expect(styleOf(row).alignItems).toBe('flex-start');

    // The theorem above only applies to DIRECT children of the flex row, so
    // pin that shape rather than assume it.
    const cells = Array.from(row.children);
    expect(cells).toHaveLength(2);
    for (const cell of cells) {
      expect(cell.classList.contains('cover-cell')).toBe(true);
      // Nothing overrides the row's top alignment per-item.
      expect(TOP_ALIGNING).toContain(styleOf(cell).alignSelf);
      // In-flow, so the two cells cannot overlap each other — the failure the
      // user actually saw was boxes sitting on top of one another.
      expect(styleOf(cell).position).toBe('static');
    }
  });

  it('keeps the two crops at their different heights — the reason alignment matters', () => {
    // If these ever became equal the alignment bug would be invisible rather
    // than fixed, and the test above would stop proving anything. Stating the
    // heights here keeps the premise honest.
    const { container } = renderPicker({ vertical: '10', horizontal: '11' });
    const row = readRow(container);

    const vertical = row.querySelector('.cover-slot.v');
    const horizontal = row.querySelector('.cover-slot.h');
    if (!vertical || !horizontal) throw new Error('cover slots missing');

    expect(styleOf(vertical).height).toBe('120px');
    expect(styleOf(horizontal).height).toBe('96px');
    // Both stay in flow inside their own cell.
    expect(styleOf(vertical).position).toBe('relative');
    expect(styleOf(horizontal).position).toBe('relative');
  });

  it('captions each crop UNDER its box, in both the filled and empty states', () => {
    for (const value of [{ vertical: '10', horizontal: '11' }, null]) {
      const { container, unmount } = renderPicker(value);
      const row = readRow(container);
      const cells = Array.from(row.children);

      for (const cell of cells) {
        // Column direction + caption as the second child is what puts the
        // label below the box rather than over it.
        expect(styleOf(cell).flexDirection).toBe('column');
        expect(cell.children).toHaveLength(2);
        expect(cell.children[0].classList.contains('cover-slot')).toBe(true);
        expect(cell.children[1].classList.contains('cover-cap')).toBe(true);
      }

      // Ratios are named on screen, the way the platform's uploader names them.
      expect(cells[0].textContent).toContain('Vertical 3:4');
      expect(cells[1].textContent).toContain('Horizontal 4:3');

      unmount();
    }
  });
});
