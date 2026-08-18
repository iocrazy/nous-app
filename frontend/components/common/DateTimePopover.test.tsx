import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';

import enJson from '../../public/locales/en.json';
import zhJson from '../../public/locales/zh.json';
import {
  CALENDAR_BODY_HEIGHT,
  CALENDAR_GRID_WIDTH,
  DateTimePopover,
  monthGrid,
} from './DateTimePopover';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('monthGrid', () => {
  it('returns a stable 6-week (42-cell) grid with leading/trailing filler days', () => {
    // July 2026 (0-based month = 6): July 1 2026 is a Wednesday.
    const cells = monthGrid(2026, 6);
    expect(cells).toHaveLength(42);
    // 3 leading days from June (Sun/Mon/Tue before Wed July 1).
    expect(cells[0].iso).toBe('2026-06-28');
    expect(cells[0].inMonth).toBe(false);
    expect(cells[2].iso).toBe('2026-06-30');
    expect(cells[2].inMonth).toBe(false);
    expect(cells[3].iso).toBe('2026-07-01');
    expect(cells[3].inMonth).toBe(true);
    const inMonthCount = cells.filter((c) => c.inMonth).length;
    expect(inMonthCount).toBe(31); // July has 31 days
    // Trailing filler from August fills out the remaining cells.
    expect(cells[41].inMonth).toBe(false);
  });

  it('handles leap-year February correctly (Feb 2024 has 29 days)', () => {
    const cells = monthGrid(2024, 1); // February, 0-based
    expect(cells).toHaveLength(42);
    const febCells = cells.filter((c) => c.inMonth);
    expect(febCells).toHaveLength(29);
    expect(febCells[0].iso).toBe('2024-02-01');
    expect(febCells[28].iso).toBe('2024-02-29');
    // Feb 1 2024 is a Thursday -> 4 leading days from January.
    expect(cells[0].iso).toBe('2024-01-28');
    expect(cells[0].inMonth).toBe(false);
  });
});

describe('DateTimePopover — range mode', () => {
  let anchor: HTMLButtonElement;

  beforeEach(() => {
    anchor = document.createElement('button');
    document.body.appendChild(anchor);
  });

  afterEach(() => {
    anchor.remove();
  });

  it('renders null (no portal) when anchorEl is null', () => {
    const { container } = render(
      <DateTimePopover anchorEl={null} start={null} end={null} onChange={vi.fn()} onClose={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('date-time-popover')).toBeNull();
  });

  it('renders into document.body via portal when anchored, with position:fixed', () => {
    render(
      <DateTimePopover
        anchorEl={anchor}
        start="2026-07-15"
        end="2026-08-03"
        onChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const pop = screen.getByTestId('date-time-popover');
    expect(pop.parentElement).toBe(document.body);
    expect(pop.style.position).toBe('fixed');
  });

  /**
   * CONTRACT — `role="dialog"` on the portal root is load-bearing, not decor.
   *
   * `components/resources/filter/FilterChip.tsx` closes its dropdown on any
   * mousedown outside its own subtree, and this popover portals to
   * `document.body`, so every click inside it lands "outside". FilterChip's
   * `inFloatingLayer` guard is what stops that from unmounting the dropdown
   * mid-pick, and it identifies the layer above it BY THIS ATTRIBUTE.
   *
   * Remove or rename it and the Resources "Date added → Custom range" filter
   * breaks: the dropdown closes the instant the user clicks the first calendar
   * day, with nothing failing anywhere near this file. Change it only together
   * with FilterChip's guard.
   */
  it('CONTRACT: the portal root carries role="dialog" (FilterChip\'s outside-click guard keys off it)', () => {
    render(
      <DateTimePopover
        anchorEl={anchor}
        start={null}
        end={null}
        onChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const pop = screen.getByTestId('date-time-popover');
    expect(pop).toHaveAttribute('role', 'dialog');
    // The guard walks up with `closest()`, so the attribute has to sit on the
    // portal ROOT — a descendant carrying it would not cover clicks on days.
    expect(pop.closest('[role="dialog"]')).toBe(pop);
  });

  it('shows the month derived from `start` on open, with the committed range read out', () => {
    render(
      <DateTimePopover
        anchorEl={anchor}
        start="2026-07-15"
        end="2026-08-03"
        onChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    // Month view derives from `start` (2026-07), so July 15 is directly clickable.
    expect(screen.getByRole('button', { name: '2026-07-15' })).toBeTruthy();
    expect(screen.getByTestId('date-range-start-cell')).toHaveTextContent('2026-07-15');
    expect(screen.getByTestId('date-range-end-cell')).toHaveTextContent('2026-08-03');
  });

  it('falls back to today\'s month when `start` is null', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 1, 12, 0, 0)); // July 2026
    render(
      <DateTimePopover anchorEl={anchor} start={null} end={null} onChange={vi.fn()} onClose={vi.fn()} />,
    );
    expect(screen.getByRole('button', { name: '2026-07-15' })).toBeTruthy();
  });

  it('commit-on-complete: first click buffers a draft start (no onChange yet, but visually selected)', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 1, 12, 0, 0)); // July 2026, so start=null opens on July.
    const onChange = vi.fn();
    render(
      <DateTimePopover anchorEl={anchor} start={null} end={null} onChange={onChange} onClose={vi.fn()} />,
    );
    const day15 = screen.getByRole('button', { name: '2026-07-15' });
    fireEvent.click(day15);
    // A half-open range must never reach a caller that treats onChange as
    // "apply this" (Task 9 inline PATCH / Task 10 settings form).
    expect(onChange).not.toHaveBeenCalled();
    // But the draft endpoint is still visually selected (agent solid).
    expect(day15.className).toContain('bg-agent');
    expect(day15.className).toContain('text-white');
  });

  it('two clicks select a cross-month range in order, committing only on the second (completing) click', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 1, 12, 0, 0)); // July 2026, so start=null opens on July.
    const onChange = vi.fn();
    render(
      <DateTimePopover anchorEl={anchor} start={null} end={null} onChange={onChange} onClose={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole('button', { name: '2026-07-15' }));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText('Next Month'));
    fireEvent.click(screen.getByRole('button', { name: '2026-08-03' }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith('2026-07-15', '2026-08-03');
  });

  it('swaps start/end when the second click lands earlier than the draft start, committing once', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 1, 12, 0, 0)); // July 2026
    const onChange = vi.fn();
    render(
      <DateTimePopover
        anchorEl={anchor}
        start={null}
        end={null}
        onChange={onChange}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '2026-07-20' }));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '2026-07-10' }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith('2026-07-10', '2026-07-20');
  });

  it('a click after a complete range starts a fresh draft (no onChange until it completes again)', () => {
    const onChange = vi.fn();
    render(
      <DateTimePopover
        anchorEl={anchor}
        start="2026-07-05"
        end="2026-07-10"
        onChange={onChange}
        onClose={vi.fn()}
      />,
    );
    const day20 = screen.getByRole('button', { name: '2026-07-20' });
    fireEvent.click(day20);
    expect(onChange).not.toHaveBeenCalled();
    expect(day20.className).toContain('bg-agent');
    expect(screen.getByTestId('date-range-start-cell')).toHaveTextContent('2026-07-20');
    expect(screen.getByTestId('date-range-end-cell')).not.toHaveTextContent('2026-07-10');
  });

  it('closing on a half-selected draft (Escape or outside click) discards it without calling onChange', async () => {
    const onChangeEsc = vi.fn();
    const onCloseEsc = vi.fn();
    const { unmount } = render(
      <DateTimePopover
        anchorEl={anchor}
        start={null}
        end={null}
        onChange={onChangeEsc}
        onClose={onCloseEsc}
      />,
    );
    fireEvent.click(screen.getAllByRole('button', { name: /^\d{4}-\d{2}-\d{2}$/ })[10]);
    expect(onChangeEsc).not.toHaveBeenCalled();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onCloseEsc).toHaveBeenCalledTimes(1);
    expect(onChangeEsc).not.toHaveBeenCalled();
    unmount();

    const onChangeOutside = vi.fn();
    const onCloseOutside = vi.fn();
    render(
      <DateTimePopover
        anchorEl={anchor}
        start={null}
        end={null}
        onChange={onChangeOutside}
        onClose={onCloseOutside}
      />,
    );
    fireEvent.click(screen.getAllByRole('button', { name: /^\d{4}-\d{2}-\d{2}$/ })[10]);
    expect(onChangeOutside).not.toHaveBeenCalled();
    await new Promise((r) => setTimeout(r, 0));
    const outside = document.createElement('div');
    document.body.appendChild(outside);
    fireEvent.mouseDown(outside);
    expect(onCloseOutside).toHaveBeenCalledTimes(1);
    expect(onChangeOutside).not.toHaveBeenCalled();
    outside.remove();
  });

  it('‹ › navigate months without mutating the pending selection', () => {
    render(
      <DateTimePopover
        anchorEl={anchor}
        start="2026-07-15"
        end={null}
        onChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: '2026-08-15' })).toBeNull();
    fireEvent.click(screen.getByLabelText('Next Month'));
    expect(screen.getByRole('button', { name: '2026-08-15' })).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Previous Month'));
    expect(screen.getByRole('button', { name: '2026-07-15' })).toBeTruthy();
    // Start cell is unaffected by pure navigation.
    expect(screen.getByTestId('date-range-start-cell')).toHaveTextContent('2026-07-15');
  });

  it('Clear resets both start and end to null', () => {
    const onChange = vi.fn();
    render(
      <DateTimePopover
        anchorEl={anchor}
        start="2026-07-05"
        end="2026-07-10"
        onChange={onChange}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('date-time-clear'));
    expect(onChange).toHaveBeenLastCalledWith(null, null);
    expect(screen.getByTestId('date-range-start-cell')).not.toHaveTextContent('2026-07-05');
  });

  it('Escape closes the popover', () => {
    const onClose = vi.fn();
    render(
      <DateTimePopover anchorEl={anchor} start={null} end={null} onChange={vi.fn()} onClose={onClose} />,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('an outside click closes the popover, but an inside click does not', async () => {
    const onClose = vi.fn();
    render(
      <DateTimePopover anchorEl={anchor} start={null} end={null} onChange={vi.fn()} onClose={onClose} />,
    );
    // The listener is attached via a deferred setTimeout(0) to dodge the
    // opening click; flush it first.
    await new Promise((r) => setTimeout(r, 0));

    const inside = screen.getByTestId('date-time-clear');
    fireEvent.mouseDown(inside);
    expect(onClose).not.toHaveBeenCalled();

    const outside = document.createElement('div');
    document.body.appendChild(outside);
    fireEvent.mouseDown(outside);
    expect(onClose).toHaveBeenCalledTimes(1);
    outside.remove();
  });

  it('scrolling the window closes the popover', async () => {
    const onClose = vi.fn();
    render(
      <DateTimePopover anchorEl={anchor} start={null} end={null} onChange={vi.fn()} onClose={onClose} />,
    );
    fireEvent.scroll(window);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('flips above the anchor when the measured height would overflow the bottom of the viewport', () => {
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 700 });
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 });
    anchor.getBoundingClientRect = () =>
      ({ top: 500, bottom: 520, left: 50, right: 150, width: 100, height: 20 }) as DOMRect;

    render(
      <DateTimePopover anchorEl={anchor} start={null} end={null} onChange={vi.fn()} onClose={vi.fn()} />,
    );
    const pop = screen.getByTestId('date-time-popover');
    // jsdom always reports offsetHeight 0, so stub a real measured height to
    // exercise the overflow branch deterministically.
    Object.defineProperty(pop, 'offsetHeight', { configurable: true, value: 320 });

    // Re-trigger placement (a resize is the least invasive way to call the
    // same `place()` callback the component wires up internally).
    fireEvent(window, new Event('resize'));

    // below would be bottom(520)+6=526, +h(320)=846 > innerHeight(700)-8=692 → flip above.
    // Flipped top = max(8, anchorTop(500) - 6 - h(320)) = max(8, 174) = 174.
    const top = parseFloat(pop.style.top);
    expect(top).toBeLessThan(500); // above the anchor's top (500), not below it
    expect(top).toBe(Math.max(8, 500 - 6 - 320));
  });

  it('places below the anchor +6px when there is enough room', () => {
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 1000 });
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 });
    anchor.getBoundingClientRect = () =>
      ({ top: 100, bottom: 120, left: 50, right: 150, width: 100, height: 20 }) as DOMRect;

    render(
      <DateTimePopover anchorEl={anchor} start={null} end={null} onChange={vi.fn()} onClose={vi.fn()} />,
    );
    const pop = screen.getByTestId('date-time-popover');
    Object.defineProperty(pop, 'offsetHeight', { configurable: true, value: 300 });
    fireEvent(window, new Event('resize'));

    expect(parseFloat(pop.style.top)).toBe(120 + 6);
  });
});

// ── range mode: single-end (segment) editing ──
//
// "Nudge the deadline two days" used to cost a full re-pick of BOTH ends,
// because the only way into the range was the two-click flow. The Start / End
// cells are now buttons that arm one end; the next day click writes only that
// end. The two-click flow above is untouched and is still what an un-armed
// popover does.

describe('DateTimePopover — range segment editing', () => {
  let anchor: HTMLButtonElement;

  beforeEach(() => {
    anchor = document.createElement('button');
    document.body.appendChild(anchor);
  });

  afterEach(() => {
    anchor.remove();
  });

  const renderRange = (
    start: string | null,
    end: string | null,
    onChange = vi.fn(),
  ) => {
    render(
      <DateTimePopover
        anchorEl={anchor}
        start={start}
        end={end}
        onChange={onChange}
        onClose={vi.fn()}
      />,
    );
    return onChange;
  };

  it('opens un-armed, arms the pressed end, and disarms when pressed again', () => {
    renderRange('2026-07-05', '2026-07-10');
    const startCell = screen.getByTestId('date-range-start-cell');
    const endCell = screen.getByTestId('date-range-end-cell');
    // Default is the two-click flow — neither end is armed on open.
    expect(startCell).toHaveAttribute('aria-pressed', 'false');
    expect(endCell).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(startCell);
    expect(startCell).toHaveAttribute('aria-pressed', 'true');
    expect(endCell).toHaveAttribute('aria-pressed', 'false');

    // Pressing the armed cell is the way back out, with nothing committed.
    fireEvent.click(startCell);
    expect(startCell).toHaveAttribute('aria-pressed', 'false');
  });

  it('arming the other end moves the arm rather than arming both', () => {
    renderRange('2026-07-05', '2026-07-10');
    fireEvent.click(screen.getByTestId('date-range-start-cell'));
    fireEvent.click(screen.getByTestId('date-range-end-cell'));
    expect(screen.getByTestId('date-range-start-cell')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('date-range-end-cell')).toHaveAttribute('aria-pressed', 'true');
  });

  it('armed start: one click rewrites start and commits, leaving end exactly as it was', () => {
    const onChange = renderRange('2026-07-05', '2026-07-10');
    fireEvent.click(screen.getByTestId('date-range-start-cell'));
    fireEvent.click(screen.getByRole('button', { name: '2026-07-03' }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith('2026-07-03', '2026-07-10');
    expect(screen.getByTestId('date-range-end-cell')).toHaveTextContent('2026-07-10');
  });

  it('armed end: one click rewrites end and commits, leaving start exactly as it was', () => {
    const onChange = renderRange('2026-07-05', '2026-07-10');
    fireEvent.click(screen.getByTestId('date-range-end-cell'));
    fireEvent.click(screen.getByRole('button', { name: '2026-07-22' }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith('2026-07-05', '2026-07-22');
    expect(screen.getByTestId('date-range-start-cell')).toHaveTextContent('2026-07-05');
  });

  it('an armed segment with nothing on the other side is unconstrained', () => {
    // Start held, end empty — the state the schedule cell opens in most often.
    const onChange = renderRange('2026-07-05', null);
    fireEvent.click(screen.getByTestId('date-range-start-cell'));
    // No end to invert against, so even a far-future day is live.
    expect(screen.getByRole('button', { name: '2026-07-28' })).not.toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '2026-07-28' }));
    expect(onChange).toHaveBeenLastCalledWith('2026-07-28', null);
  });

  it('disables the days that would invert the range instead of dragging the other end along', () => {
    const onChange = renderRange('2026-07-05', '2026-07-10');

    fireEvent.click(screen.getByTestId('date-range-start-cell'));
    // A start after the held end is impossible — and it is greyed out, not
    // accepted-then-silently-clamped. The end the user did not touch stays put.
    expect(screen.getByRole('button', { name: '2026-07-20' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '2026-07-10' })).not.toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '2026-07-20' }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByTestId('date-range-end-cell')).toHaveTextContent('2026-07-10');

    // Same rule mirrored on the other end.
    fireEvent.click(screen.getByTestId('date-range-end-cell'));
    expect(screen.getByRole('button', { name: '2026-07-01' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '2026-07-05' })).not.toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '2026-07-01' }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByTestId('date-range-start-cell')).toHaveTextContent('2026-07-05');
  });

  it('arming jumps the calendar to the month that end already lives in', () => {
    renderRange('2026-07-15', '2026-09-03');
    // Opens on the start's month.
    expect(screen.getByRole('button', { name: '2026-07-15' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '2026-09-03' })).toBeNull();

    fireEvent.click(screen.getByTestId('date-range-end-cell'));
    expect(screen.getByRole('button', { name: '2026-09-03' })).toBeTruthy();

    // Back to the start's month when the start is armed instead.
    fireEvent.click(screen.getByTestId('date-range-start-cell'));
    expect(screen.getByRole('button', { name: '2026-07-15' })).toBeTruthy();
  });

  it('arming an empty end leaves the current month alone (nothing to jump to)', () => {
    renderRange('2026-07-15', null);
    fireEvent.click(screen.getByTestId('date-range-end-cell'));
    expect(screen.getByRole('button', { name: '2026-07-15' })).toBeTruthy();
  });

  it('disarms after the single-end commit, so the very next clicks are the two-click flow again', () => {
    const onChange = renderRange('2026-07-05', '2026-07-10');
    fireEvent.click(screen.getByTestId('date-range-start-cell'));
    fireEvent.click(screen.getByRole('button', { name: '2026-07-03' }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('date-range-start-cell')).toHaveAttribute('aria-pressed', 'false');

    // Two-click flow: the first click only drafts...
    fireEvent.click(screen.getByRole('button', { name: '2026-07-18' }));
    expect(onChange).toHaveBeenCalledTimes(1);
    // ...the second completes and commits BOTH ends.
    fireEvent.click(screen.getByRole('button', { name: '2026-07-25' }));
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(onChange).toHaveBeenLastCalledWith('2026-07-18', '2026-07-25');
  });

  // Clear follows the arming, because a half-bounded range ("after Aug 1, no
  // upper bound") has to stay reachable: the two native <input type="date">
  // fields this control replaced could each be emptied on their own, and a
  // single all-or-nothing Clear would have quietly taken that away.
  it('Clear with END armed empties only the end, leaving start untouched', () => {
    const onChange = renderRange('2026-07-05', '2026-07-10');
    fireEvent.click(screen.getByTestId('date-range-end-cell'));
    expect(screen.getByTestId('date-range-end-cell')).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByTestId('date-time-clear'));
    expect(onChange).toHaveBeenLastCalledWith('2026-07-05', null);
    // Disarms, exactly like a segment day-click does.
    expect(screen.getByTestId('date-range-end-cell')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('date-range-start-cell')).toHaveTextContent('2026-07-05');
    expect(screen.getByTestId('date-range-end-cell')).toHaveTextContent('–');
  });

  it('Clear with START armed empties only the start, leaving end untouched', () => {
    const onChange = renderRange('2026-07-05', '2026-07-10');
    fireEvent.click(screen.getByTestId('date-range-start-cell'));

    fireEvent.click(screen.getByTestId('date-time-clear'));
    expect(onChange).toHaveBeenLastCalledWith(null, '2026-07-10');
    expect(screen.getByTestId('date-range-start-cell')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('date-range-end-cell')).toHaveTextContent('2026-07-10');
  });

  it('Clear with NOTHING armed still empties both ends and returns to the two-click flow', () => {
    const onChange = renderRange('2026-07-05', '2026-07-10');

    fireEvent.click(screen.getByTestId('date-time-clear'));
    expect(onChange).toHaveBeenLastCalledWith(null, null);
    expect(screen.getByTestId('date-range-end-cell')).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(screen.getByRole('button', { name: '2026-07-18' }));
    expect(onChange).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '2026-07-25' }));
    expect(onChange).toHaveBeenLastCalledWith('2026-07-18', '2026-07-25');
  });

  it('the Clear label names the end it would empty', () => {
    renderRange('2026-07-05', '2026-07-10');
    const clear = screen.getByTestId('date-time-clear');
    // A button that says plain "Clear" while it would only empty one end is a
    // button that lies about what it is about to do.
    expect(clear).toHaveTextContent('Clear');

    fireEvent.click(screen.getByTestId('date-range-start-cell'));
    expect(clear).toHaveTextContent('Clear start');

    fireEvent.click(screen.getByTestId('date-range-start-cell')); // disarm
    expect(clear).toHaveTextContent('Clear');

    fireEvent.click(screen.getByTestId('date-range-end-cell'));
    expect(clear).toHaveTextContent('Clear end');
  });

  it('still honours the minAt/maxAt window while a segment is armed', () => {
    render(
      <DateTimePopover
        anchorEl={anchor}
        start="2026-07-15"
        end="2026-07-20"
        minAt={new Date(2026, 6, 10, 0, 0)}
        onChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('date-range-start-cell'));
    // Blocked by the window (below the floor), not by the segment rule.
    expect(screen.getByRole('button', { name: '2026-07-09' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '2026-07-11' })).not.toBeDisabled();
  });
});

// ── single mode: one point in time, optional clock, hard window ──
//
// This is the half `DateRangePopover` never had, and `DateTimePicker` had but
// nobody could reach (one caller, hardcoded English month/weekday names). The
// tests below are what "the publish module is just the canvas control plus a
// time" has to mean concretely.

describe('DateTimePopover — single mode', () => {
  let anchor: HTMLButtonElement;

  beforeEach(() => {
    anchor = document.createElement('button');
    document.body.appendChild(anchor);
  });

  afterEach(() => {
    anchor.remove();
  });

  it('commits a bare date and closes when there is no time dimension', () => {
    const onChange = vi.fn();
    const onClose = vi.fn();
    render(
      <DateTimePopover
        mode="single"
        anchorEl={anchor}
        value="2026-07-15"
        onChange={onChange}
        onClose={onClose}
      />,
    );
    // No clock: no time columns, no Done — one click is the whole interaction.
    expect(screen.queryByTestId('date-time-columns')).toBeNull();
    expect(screen.queryByTestId('date-time-done')).toBeNull();

    fireEvent.click(screen.getByLabelText('2026-07-20'));
    expect(onChange).toHaveBeenCalledWith('2026-07-20');
    expect(onClose).toHaveBeenCalled();
  });

  it('commits the datetime-local shape and stays open while the time is set', () => {
    const onChange = vi.fn();
    const onClose = vi.fn();
    render(
      <DateTimePopover
        mode="single"
        withTime
        anchorEl={anchor}
        value="2026-07-15T08:30"
        onChange={onChange}
        onClose={onClose}
      />,
    );
    // The committed value's own time is what the columns open on.
    expect(screen.getByTestId('date-time-value-cell')).toHaveTextContent('2026-07-15 08:30');

    fireEvent.click(screen.getByLabelText('2026-07-20'));
    expect(onChange).toHaveBeenLastCalledWith('2026-07-20T08:30');
    // Closing on the day click would put the clock out of reach.
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('date-time-hour-21'));
    expect(onChange).toHaveBeenLastCalledWith('2026-07-20T21:30');
    fireEvent.click(screen.getByTestId('date-time-minute-45'));
    expect(onChange).toHaveBeenLastCalledWith('2026-07-20T21:45');

    fireEvent.click(screen.getByTestId('date-time-done'));
    expect(onClose).toHaveBeenCalled();
  });

  it('disables everything outside [minAt, maxAt] instead of accepting then complaining', () => {
    // Window: 2026-07-15 14:20 → 2026-07-18 09:00.
    render(
      <DateTimePopover
        mode="single"
        withTime
        anchorEl={anchor}
        value={null}
        minAt={new Date(2026, 6, 15, 14, 20)}
        maxAt={new Date(2026, 6, 18, 9, 0)}
        onChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('2026-07-14')).toBeDisabled();
    expect(screen.getByLabelText('2026-07-15')).not.toBeDisabled();
    expect(screen.getByLabelText('2026-07-18')).not.toBeDisabled();
    expect(screen.getByLabelText('2026-07-19')).toBeDisabled();

    // Month nav is bounded too — paging into a month of dead cells is not a
    // feature, it is a dead end that looks like one.
    expect(screen.getByLabelText('Previous Month')).toBeDisabled();
    expect(screen.getByLabelText('Next Month')).toBeDisabled();

    // On the floor day, hours below the floor are dead; on the ceiling day the
    // dead ones are at the other end. Same window, both directions.
    fireEvent.click(screen.getByLabelText('2026-07-15'));
    expect(screen.getByTestId('date-time-hour-14')).not.toBeDisabled();
    expect(screen.getByTestId('date-time-hour-13')).toBeDisabled();
    fireEvent.click(screen.getByLabelText('2026-07-18'));
    expect(screen.getByTestId('date-time-hour-09')).not.toBeDisabled();
    expect(screen.getByTestId('date-time-hour-10')).toBeDisabled();
  });

  it('snaps the held time forward when the picked day would make it illegal', () => {
    const onChange = vi.fn();
    render(
      <DateTimePopover
        mode="single"
        withTime
        anchorEl={anchor}
        // 06:00 is fine on the 16th and impossible on the 15th.
        value="2026-07-16T06:00"
        minAt={new Date(2026, 6, 15, 14, 20)}
        maxAt={new Date(2026, 6, 18, 9, 0)}
        onChange={onChange}
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByLabelText('2026-07-15'));
    // 14:20 is on the 5-minute grid, so it is itself the first legal slot.
    expect(onChange).toHaveBeenLastCalledWith('2026-07-15T14:20');
  });

  it('offers shortcuts, and greys out the ones the window forbids', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 15, 10, 0, 0));
    const onChange = vi.fn();
    render(
      <DateTimePopover
        mode="single"
        withTime
        quickOptions
        anchorEl={anchor}
        value={null}
        // Only today and tomorrow are reachable.
        maxAt={new Date(2026, 6, 16, 23, 0)}
        onChange={onChange}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Today' })).not.toBeDisabled();
    expect(screen.getByRole('button', { name: 'Tomorrow' })).not.toBeDisabled();
    expect(screen.getByRole('button', { name: 'In 3 Days' })).toBeDisabled();
    // 2026-07-15 is a Wednesday, so "This Sunday" is the 19th — past the ceiling.
    expect(screen.getByRole('button', { name: 'This Sunday' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Tomorrow' }));
    expect(onChange).toHaveBeenLastCalledWith(expect.stringContaining('2026-07-16T'));
  });

  it('clears to null without closing the caller out of a fresh pick', () => {
    const onChange = vi.fn();
    render(
      <DateTimePopover
        mode="single"
        withTime
        anchorEl={anchor}
        value="2026-07-15T08:30"
        onChange={onChange}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('date-time-clear'));
    expect(onChange).toHaveBeenCalledWith(null);
    expect(screen.getByTestId('date-time-value-cell')).toHaveTextContent('–');
  });
});

// ── the reason the native input had to go ──
//
// `<input type="datetime-local">` renders its own chrome from the BROWSER's
// locale, so in a zh-CN browser it reads `mm/dd/yyyy, --:-- --` and no locale
// file of ours can touch it. Rendering this component under the real shipped
// locale JSON is the falsifiable version of "it speaks Chinese now": every
// visible word comes from zh.json, and the value read-out is ISO, never m/d/y.

function makeI18n(lng: 'en' | 'zh'): I18n {
  const inst = createInstance();
  void inst.use(initReactI18next).init({
    lng,
    fallbackLng: 'en',
    resources: { en: { translation: enJson }, zh: { translation: zhJson } },
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
  });
  return inst;
}

describe('DateTimePopover — locale', () => {
  let anchor: HTMLButtonElement;

  beforeEach(() => {
    anchor = document.createElement('button');
    document.body.appendChild(anchor);
  });

  afterEach(() => {
    anchor.remove();
  });

  const renderUnder = (lng: 'en' | 'zh') =>
    render(
      <I18nextProvider i18n={makeI18n(lng)}>
        <DateTimePopover
          mode="single"
          withTime
          quickOptions
          anchorEl={anchor}
          value="2026-07-15T08:30"
          onChange={vi.fn()}
          onClose={vi.fn()}
        />
      </I18nextProvider>,
    );

  it('renders every label from zh.json, with no English or m/d/y left over', () => {
    renderUnder('zh');
    // The popover is a portal onto document.body, so the render container is
    // empty by design — read the popover itself.
    const text = screen.getByTestId('date-time-popover').textContent ?? '';

    expect(screen.getByText('2026年 7月')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '今天' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '本周日' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '完成' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '清除' })).toBeInTheDocument();
    // Weekday headers: the old DateTimePicker hardcoded Su/Mo/Tu…
    expect(screen.getByText('日')).toBeInTheDocument();
    // The value read-out is ISO, so no `07/15/2026` and no `mm/dd/yyyy`.
    expect(text).toContain('2026-07-15 08:30');
    expect(text).not.toMatch(/\d{2}\/\d{2}\/\d{4}/);
    expect(text).not.toMatch(/mm\/dd\/yyyy/i);
    // No raw i18n key leaked (the failure mode when a key exists in en only).
    expect(text).not.toMatch(/\bcommon\.date(Range|Time)Popover\.[a-zA-Z]/);
  });

  it('renders the English labels under en, from the same keys', () => {
    renderUnder('en');
    expect(screen.getByText('July 2026')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'This Sunday' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Done' })).toBeInTheDocument();
  });
});

describe('DateTimePopover geometry', () => {
  // jsdom does not lay anything out, so no rendering test can catch a calendar
  // that is too narrow for its own grid — the first release shipped exactly
  // that (CALENDAR_WIDTH 260 gave the grid 236px against the 248px its seven
  // `w-8` cells and six `gap-1` tracks need, and the month rendered as a
  // run-on smear). What *is* checkable is the arithmetic the layout rests on,
  // so it is stated here rather than left to a visual pass someone has to
  // remember to do.
  const DAY_CELL = 32; // `w-8` on the day buttons
  const DAY_GAP = 4; // `gap-1` between grid tracks
  const COLUMNS = 7;

  it('gives the month grid at least the width its seven cells occupy', () => {
    const needed = DAY_CELL * COLUMNS + DAY_GAP * (COLUMNS - 1);
    expect(CALENDAR_GRID_WIDTH).toBeGreaterThanOrEqual(needed);
  });

  it('keeps the grid width in step with the cell size it is derived from', () => {
    // Pins the *relationship*, not the number: bump `w-8` to `w-9` without
    // touching the constant and this fails instead of silently re-crushing the
    // month. The class names live in the component; this is the arithmetic.
    expect(CALENDAR_GRID_WIDTH).toBe(DAY_CELL * COLUMNS + DAY_GAP * (COLUMNS - 1));
  });

  // ------------------------------------------------------------------
  // The vertical axis — the one that shipped wrong.
  //
  // The time columns carried a hand-tuned `max-h-[228px]` while the calendar
  // beside them is 272px tall, and the row is a plain flex row: the time
  // WRAPPER stretched to 272 while its two scroll columns stopped at 228,
  // leaving a 44px dead box under the minute column. jsdom lays nothing out,
  // so no test can *see* that box — but the two declared heights are both
  // readable from the rendered DOM, and them disagreeing IS the bug.
  //
  // These tests read the height the component actually declares (inline style
  // OR a Tailwind arbitrary-value class, so re-introducing a `max-h-[Npx]`
  // cap is caught rather than skipped) and compare it against the calendar
  // height computed from the ROWS THE COMPONENT REALLY RENDERED.
  // ------------------------------------------------------------------
  const NAV_ROW = 24; // `h-6` on the month-nav row
  const NAV_ROW_MB = 8; // `mb-2` under it
  const WEEKDAY_ROW = 24; // `h-6` on the weekday header cells
  const WEEKDAY_ROW_MB = 4; // `mb-1` under it

  /**
   * The height this element declares for itself, in px — from the inline
   * style, or from a `h-[Npx]` / `max-h-[Npx]` Tailwind arbitrary value.
   * Returns null when it declares none. A `max-h` counts: the browser applies
   * it against the stretched height, so a cap below the calendar's height is
   * exactly the shipped bug and must not read as "declares nothing".
   */
  const declaredHeightPx = (el: HTMLElement): number | null => {
    const inline = el.style.height;
    if (inline.endsWith('px')) return Number.parseFloat(inline);
    const cls = /(?:^|\s)(?:max-)?h-\[(\d+(?:\.\d+)?)px\]/.exec(el.className);
    return cls ? Number.parseFloat(cls[1]) : null;
  };

  /** Height of the calendar column, measured off the DOM it actually built. */
  const renderedCalendarHeight = (): number => {
    const dayButtons = Array.from(
      document.body.querySelectorAll<HTMLButtonElement>('button[aria-label]'),
    ).filter((b) => /^\d{4}-\d{2}-\d{2}$/.test(b.getAttribute('aria-label') ?? ''));
    expect(dayButtons.length).toBeGreaterThan(0);
    expect(dayButtons.length % COLUMNS).toBe(0);
    const rows = dayButtons.length / COLUMNS;
    return (
      NAV_ROW
      + NAV_ROW_MB
      + WEEKDAY_ROW
      + WEEKDAY_ROW_MB
      + DAY_CELL * rows
      + DAY_GAP * (rows - 1)
    );
  };

  const openWithTime = (): { hours: HTMLElement; minutes: HTMLElement } => {
    const anchor = document.createElement('button');
    document.body.appendChild(anchor);
    render(
      <DateTimePopover
        mode="single"
        withTime
        anchorEl={anchor}
        value="2026-08-17T09:30"
        onChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const columns = screen.getByTestId('date-time-columns');
    const [hours, minutes] = Array.from(columns.children) as HTMLElement[];
    expect(hours).toBeTruthy();
    expect(minutes).toBeTruthy();
    return { hours, minutes };
  };

  it('gives the hour and minute columns the SAME height as the calendar beside them', () => {
    // Falsifiable: with the shipped `max-h-[228px]` this reads 228 against a
    // 272px calendar and fails, naming the 44px blank in the failure message.
    const { hours, minutes } = openWithTime();
    const calendar = renderedCalendarHeight();
    expect(declaredHeightPx(hours)).toBe(calendar);
    expect(declaredHeightPx(minutes)).toBe(calendar);
  });

  it('lets neither time column cap itself shorter than the calendar', () => {
    // The `max-h` half stated on its own: a cap below the calendar height is
    // what leaves the dead box, whatever else the element declares.
    const { hours, minutes } = openWithTime();
    const calendar = renderedCalendarHeight();
    for (const col of [hours, minutes]) {
      const cap = /(?:^|\s)max-h-\[(\d+(?:\.\d+)?)px\]/.exec(col.className);
      if (cap) expect(Number.parseFloat(cap[1])).toBeGreaterThanOrEqual(calendar);
    }
  });

  it('derives the exported height from the grid the component renders, not a literal', () => {
    // Ties the constant to the real row count: change `monthGrid` to five rows,
    // or `h-8` to `h-9`, and this fails instead of silently re-opening the gap.
    openWithTime();
    expect(CALENDAR_BODY_HEIGHT).toBe(renderedCalendarHeight());
  });
});
