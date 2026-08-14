/**
 * Date-added chip — the Custom-range editor is the app-wide DateTimePopover,
 * not two native `<input type="date">` fields.
 *
 * The regression this file exists for: DateTimePopover renders through
 * `createPortal(document.body)`, so every click inside it lands OUTSIDE the
 * FilterChip's `rootRef`. FilterChip closes on any outside mousedown, so
 * without the `inFloatingLayer` guard the first click on a calendar day
 * unmounts the whole dropdown mid-pick — the picker would be unusable while
 * looking perfectly fine in isolation. The nesting is therefore asserted with
 * the REAL FilterChip around the REAL dropdown, not with either one stubbed.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import { useState } from 'react';

import enJson from '../../../public/locales/en.json';
import { DateAddedFilterDropdown } from './DateAddedFilterDropdown';
import { FilterChip } from './FilterChip';
import type { DateAddedChipValue } from './types';

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

const CUSTOM: DateAddedChipValue = {
  preset: 'custom',
  customAfter: null,
  customBefore: null,
};

/** Bare dropdown, no chip around it — for the value-plumbing assertions. */
function renderBare(value: DateAddedChipValue = CUSTOM) {
  const onChange = vi.fn();
  render(
    <I18nextProvider i18n={makeI18n()}>
      <DateAddedFilterDropdown value={value} onChange={onChange} onClearAll={vi.fn()} />
    </I18nextProvider>,
  );
  return { onChange };
}

/** The real chip wrapping the real dropdown — for the nesting assertions. */
function Nested({ onClose }: { onClose: () => void }) {
  const [value, setValue] = useState<DateAddedChipValue>(CUSTOM);
  return (
    <I18nextProvider i18n={makeI18n()}>
      <FilterChip
        chipId="date_added"
        label="Date added"
        isActive
        isOpen
        onToggle={vi.fn()}
        onClose={onClose}
      >
        <DateAddedFilterDropdown value={value} onChange={setValue} onClearAll={vi.fn()} />
      </FilterChip>
    </I18nextProvider>
  );
}

describe('DateAddedFilterDropdown — custom range via DateTimePopover', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date(2026, 7, 13, 10, 0, 0)); // local 2026-08-13
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('the From trigger opens the popover and a completed range emits both bounds', () => {
    const { onChange } = renderBare();
    fireEvent.click(screen.getByTestId('filter-date-after'));
    expect(screen.getByTestId('date-time-popover')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('2026-08-04'));
    // Commit-on-complete: a half-open range must never reach the caller.
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText('2026-08-11'));
    expect(onChange).toHaveBeenCalledWith({
      preset: 'custom',
      customAfter: '2026-08-04',
      customBefore: '2026-08-11',
    });
  });

  it('the To trigger opens the same picker', () => {
    renderBare();
    fireEvent.click(screen.getByTestId('filter-date-before'));
    expect(screen.getByTestId('date-time-popover')).toBeTruthy();
  });

  it('Clear empties both bounds, keeping the custom preset', () => {
    const { onChange } = renderBare({
      preset: 'custom',
      customAfter: '2026-08-04',
      customBefore: '2026-08-11',
    });
    fireEvent.click(screen.getByTestId('filter-date-after'));
    fireEvent.click(screen.getByTestId('date-time-clear'));
    expect(onChange).toHaveBeenCalledWith({
      preset: 'custom',
      customAfter: null,
      customBefore: null,
    });
  });

  it('shows the picked bounds on the triggers, and an en-dash when unset', () => {
    renderBare({ preset: 'custom', customAfter: '2026-08-04', customBefore: null });
    expect(screen.getByTestId('filter-date-after').textContent).toBe('2026-08-04');
    expect(screen.getByTestId('filter-date-before').textContent).toBe('–');
  });
});

describe('DateAddedFilterDropdown — nested inside FilterChip', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date(2026, 7, 13, 10, 0, 0));
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('clicking inside the portalled popover does NOT close the chip dropdown', () => {
    const onClose = vi.fn();
    render(<Nested onClose={onClose} />);

    fireEvent.mouseDown(screen.getByTestId('filter-date-after'));
    fireEvent.click(screen.getByTestId('filter-date-after'));
    vi.advanceTimersByTime(1); // the popover arms its own outside-click listener on a 0ms timer

    const day = screen.getByLabelText('2026-08-04');
    fireEvent.mouseDown(day);
    fireEvent.click(day);
    expect(onClose).not.toHaveBeenCalled();
    // Still mounted — the dropdown survived the pick.
    expect(screen.getByTestId('filter-date-after')).toBeTruthy();

    const secondDay = screen.getByLabelText('2026-08-11');
    fireEvent.mouseDown(secondDay);
    fireEvent.click(secondDay);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByTestId('filter-date-after').textContent).toBe('2026-08-04');
    expect(screen.getByTestId('filter-date-before').textContent).toBe('2026-08-11');
  });

  it('a genuine outside click still closes the chip dropdown', () => {
    const onClose = vi.fn();
    render(<Nested onClose={onClose} />);
    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalled();
  });

  it('Escape dismisses the popover first, leaving the dropdown open', () => {
    const onClose = vi.fn();
    render(<Nested onClose={onClose} />);
    fireEvent.click(screen.getByTestId('filter-date-after'));
    expect(screen.getByTestId('date-time-popover')).toBeTruthy();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByTestId('date-time-popover')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();

    // With no layer above it, Escape belongs to the dropdown again.
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});
