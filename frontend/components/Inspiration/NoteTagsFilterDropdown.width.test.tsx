/**
 * The note-tag list is bounded, because its rows render text the USER wrote.
 *
 * A `truncate` row inside a `w-max` parent never truncates: `truncate` is
 * `overflow:hidden` + `text-overflow:ellipsis` + `white-space:nowrap`, and a
 * nowrap line contributes its full unbroken width to `max-content` — so the
 * BOX grows instead of the text clipping. Note tags are whatever the author
 * typed after `#` in a note, so without a ceiling one long tag pushes this
 * panel out to the viewport edge (`FilterChip`'s own ceiling is 90vw, which is
 * measured against the viewport, not against where the chip happens to sit).
 *
 * ⚠️ jsdom has no layout engine, so nothing here observes a real width or a
 * real ellipsis. What it pins is the PAIRING — a body that opts into
 * `truncate` also declares a `max-w-` — which is the part that was missing.
 * Assertions are exact-value and positive: a component that failed to render
 * has no classes at all, and a negative assertion would pass on it.
 */
import { render, screen } from '@testing-library/react';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

import { NoteTagsFilterDropdown } from './NoteTagsFilterDropdown';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
  initReactI18next: { type: '3rdParty', init: () => {} },
}));

/** Real shape of a `getTagCounts()` row: a bare string plus a number. */
const LONG_TAG = 'a-tag-someone-actually-typed-that-is-far-longer-than-any-dropdown';

const widthUtilities = (el: Element): string[] =>
  el.className.split(/\s+/).filter((c) => /^(w-|min-w-|max-w-)/.test(c));

describe('NoteTagsFilterDropdown width', () => {
  it('keeps a ceiling when a tag is longer than the panel', () => {
    render(
      <NoteTagsFilterDropdown
        tags={[{ tag: LONG_TAG, cnt: 1 }]}
        activeTag={null}
        onChange={vi.fn()}
      />,
    );

    const menu = screen.getByRole('menu', { name: 'Note tags filter' });
    expect(widthUtilities(menu)).toEqual(['w-max', 'min-w-[11rem]', 'max-w-[22rem]']);
  });

  it('asks the long row to ellipsise, which the ceiling makes possible', () => {
    render(
      <NoteTagsFilterDropdown
        tags={[{ tag: LONG_TAG, cnt: 1 }]}
        activeTag={null}
        onChange={vi.fn()}
      />,
    );

    // The full tag is still in the DOM (truncation is visual) — a "fix" that
    // shortened the string instead of bounding the box would fail here.
    const row = screen.getByRole('button', { name: new RegExp(LONG_TAG) });
    expect(row.querySelector('.truncate')?.textContent).toBe(LONG_TAG);
  });
});
