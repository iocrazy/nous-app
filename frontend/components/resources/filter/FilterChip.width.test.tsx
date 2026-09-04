/**
 * The chip dropdown is sized by its CONTENT, not by a hardcoded width.
 *
 * WHAT BROKE: `FilterChip` gave every panel `min-w-[14rem]` (224px) while each
 * dropdown body carried its own fixed `w-44` / `w-48` / `w-52` / `w-56`. Two
 * hardcoded numbers stacked: the floor won whenever the body was narrower, so
 * the Type chip (176px of content) rendered a 224px panel with ~48px of dead
 * space down its right edge, and the Rating chip (144px, already fixed by its
 * author to `w-max`) had ~80px. This is every chip on the Resources bar plus
 * the Inspiration rating chip and the TopicFilterBar chips.
 *
 * THE CONTRACT, copied from `UiSelect` (see `../../ui/UiSelect.menuWidth.test.tsx`):
 * no `width` — a floor so the panel is never narrower than its trigger, and a
 * ceiling so a long label cannot run off screen. `w-max` makes the absolutely
 * positioned panel shrink-to-fit; `min-w-full` resolves against the `relative`
 * chip root, i.e. the trigger's own width.
 *
 * ⚠️ WHAT THIS TEST CAN AND CANNOT CHECK. jsdom has no layout engine — every
 * rect is zeroes — so nothing here observes a real pixel width or real dead
 * space. What it pins is the SIZING CONTRACT the panel and each body are given,
 * which is the thing that regressed. Assertions are exact-value and positive
 * (`toEqual` over the width utilities actually present), never `not.toContain`:
 * a body that failed to render at all has no classes, and a negative assertion
 * would pass on it.
 */
import { render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import React from 'react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import { describe, expect, it, vi } from 'vitest';

import enJson from '../../../public/locales/en.json';
import { AIStatusFilterDropdown } from './AIStatusFilterDropdown';
import { AspectFilterDropdown } from './AspectFilterDropdown';
import { DateAddedFilterDropdown } from './DateAddedFilterDropdown';
import { DurationFilterDropdown } from './DurationFilterDropdown';
import { FilterChip } from './FilterChip';
import { RatingFilterDropdown } from './RatingFilterDropdown';
import { SocialFilterDropdown } from './SocialFilterDropdown';
import { SourceFilterDropdown } from './SourceFilterDropdown';
import { TypeFilterDropdown } from './TypeFilterDropdown';
import { DEFAULT_CHIP_VALUES } from './types';

function makeI18n(): I18n {
  const instance = createInstance();
  instance.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
  });
  return instance;
}

/** Every width-ish utility on an element, in the order it was written. */
const widthUtilities = (el: Element): string[] =>
  el.className
    .split(/\s+/)
    .filter((c) => /^(w-|min-w-|max-w-)/.test(c));

describe('FilterChip dropdown panel — content-sized, not pinned to a number', () => {
  it('gives the panel a floor and a ceiling and no fixed width', () => {
    render(
      <FilterChip
        chipId="probe"
        label="Probe"
        isActive={false}
        isOpen
        onToggle={vi.fn()}
        onClose={vi.fn()}
      >
        <div data-testid="chip-body">body</div>
      </FilterChip>,
    );

    const panel = screen.getByTestId('chip-body').parentElement as HTMLElement;

    // Exactly these three, nothing else:
    //   w-max      — shrink-to-fit, the fix itself
    //   min-w-full — floor: 100% of the `relative` chip root = the trigger
    //   max-w-[90vw] — ceiling: never wider than the viewport
    expect(widthUtilities(panel)).toEqual(['w-max', 'min-w-full', 'max-w-[90vw]']);
  });

  it('sizes the root to the chip, so the floor means the chip and not the page', () => {
    // The panel's floor is `min-w-full`, i.e. 100% of THIS element. A bare
    // block-level root would stretch to whatever container it was dropped in,
    // and the floor would silently become "as wide as the page". Every bar
    // today mounts chips as flex items, which are content-sized anyway — so
    // that failure mode would ship with nothing turning red. `w-fit` makes the
    // root content-sized regardless of how the caller lays it out, which is
    // why it is asserted here rather than left to the callers.
    const { container } = render(
      // A plain block container: the case no caller exercises today.
      <div>
        <FilterChip
          chipId="probe"
          label="Probe"
          isActive={false}
          isOpen
          onToggle={vi.fn()}
          onClose={vi.fn()}
        >
          <div>body</div>
        </FilterChip>
      </div>,
    );

    const root = container.querySelector('[data-chip-id="probe"]') as HTMLElement;
    expect(root.className.split(/\s+/)).toEqual(['relative', 'w-fit']);
  });
});

/**
 * Each body: a `w-max` so it tracks its content, plus its own floor so a menu
 * of very short labels still looks like a menu. The floor values differ per
 * dropdown (a form needs more room than a list of one-word options) — pinned
 * exactly so "tidying them into one number" shows up as a diff to review.
 */
const BODIES: {
  name: string;
  menuLabel: string;
  render: () => React.ReactElement;
  expected: string[];
}[] = [
  {
    name: 'TypeFilterDropdown',
    menuLabel: 'Type filter',
    render: () => (
      <TypeFilterDropdown selectedTypes={[]} onChange={vi.fn()} onClearAll={vi.fn()} />
    ),
    expected: ['w-max', 'min-w-[9rem]'],
  },
  {
    name: 'AIStatusFilterDropdown',
    menuLabel: 'AI status filter',
    render: () => (
      <AIStatusFilterDropdown
        value={DEFAULT_CHIP_VALUES.ai_status}
        onChange={vi.fn()}
        onClearAll={vi.fn()}
      />
    ),
    expected: ['w-max', 'min-w-[9rem]'],
  },
  {
    name: 'DateAddedFilterDropdown',
    menuLabel: 'Date added filter',
    render: () => (
      <DateAddedFilterDropdown
        value={DEFAULT_CHIP_VALUES.date_added}
        onChange={vi.fn()}
        onClearAll={vi.fn()}
      />
    ),
    // Wider floor: the Custom branch renders a two-row From/To form inside the
    // same panel, and that form must not be squeezed by the preset rows above.
    expected: ['w-max', 'min-w-[12rem]'],
  },
  {
    name: 'SourceFilterDropdown',
    menuLabel: 'Source filter',
    render: () => (
      <SourceFilterDropdown
        availablePlatforms={['douyin', 'bilibili']}
        selectedPlatforms={[]}
        onChange={vi.fn()}
        onClearAll={vi.fn()}
      />
    ),
    expected: ['w-max', 'min-w-[11rem]'],
  },
  {
    name: 'AspectFilterDropdown',
    menuLabel: 'Aspect filter',
    render: () => (
      <AspectFilterDropdown selectedBuckets={[]} onChange={vi.fn()} onClearAll={vi.fn()} />
    ),
    expected: ['w-max', 'min-w-[11rem]'],
  },
  {
    name: 'DurationFilterDropdown',
    menuLabel: 'Duration filter',
    render: () => (
      <DurationFilterDropdown
        value={DEFAULT_CHIP_VALUES.duration}
        onChange={vi.fn()}
        onClearAll={vi.fn()}
      />
    ),
    // Same reason as Date added — the Custom branch holds a Min/Max form.
    expected: ['w-max', 'min-w-[12rem]'],
  },
  {
    name: 'SocialFilterDropdown',
    menuLabel: 'Social filter',
    render: () => (
      <SocialFilterDropdown
        value={DEFAULT_CHIP_VALUES.social}
        onChange={vi.fn()}
        onClearAll={vi.fn()}
      />
    ),
    // The richest body: radios, four metric rows with number inputs, a
    // checkbox. Its old `w-72` becomes the floor rather than the width, so a
    // longer translation grows the panel instead of wrapping inside it.
    expected: ['w-max', 'min-w-[18rem]'],
  },
  {
    name: 'RatingFilterDropdown',
    menuLabel: 'Rating filter',
    render: () => <RatingFilterDropdown minRating={0} onChange={vi.fn()} />,
    // Already written this way by its author — pinned so the sweep does not
    // undo it.
    expected: ['w-max', 'min-w-[9rem]'],
  },
];

describe.each(BODIES)('$name', ({ menuLabel, render: renderBody, expected }) => {
  it('sizes to its content above its own floor', () => {
    render(<I18nextProvider i18n={makeI18n()}>{renderBody()}</I18nextProvider>);
    expect(widthUtilities(screen.getByRole('menu', { name: menuLabel }))).toEqual(expected);
  });
});

describe('TagsFilterDropdown — the deliberate exception', () => {
  it('keeps its explicit 520x360 box', () => {
    // Not a leftover: the body is an EagleTagBrowser (category sidebar +
    // search + grid), which needs a real canvas rather than shrink-to-fit.
    // Asserted against the source because rendering the browser pulls in tag
    // preferences the width contract has nothing to do with.
    const src = readFileSync(
      path.join(__dirname, 'TagsFilterDropdown.tsx'),
      'utf8',
    );
    expect(src).toContain('w-[520px] max-w-[90vw] h-[360px]');
  });
});
