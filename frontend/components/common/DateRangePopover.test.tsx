import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DateRangePopover, monthGrid } from './DateRangePopover';

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

describe('DateRangePopover', () => {
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
      <DateRangePopover anchorEl={null} start={null} end={null} onChange={vi.fn()} onClose={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('date-range-popover')).toBeNull();
  });

  it('renders into document.body via portal when anchored, with position:fixed', () => {
    render(
      <DateRangePopover
        anchorEl={anchor}
        start="2026-07-15"
        end="2026-08-03"
        onChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const pop = screen.getByTestId('date-range-popover');
    expect(pop.parentElement).toBe(document.body);
    expect(pop.style.position).toBe('fixed');
  });

  it('shows the month derived from `start` on open, with the committed range read out', () => {
    render(
      <DateRangePopover
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
      <DateRangePopover anchorEl={anchor} start={null} end={null} onChange={vi.fn()} onClose={vi.fn()} />,
    );
    expect(screen.getByRole('button', { name: '2026-07-15' })).toBeTruthy();
  });

  it('commit-on-complete: first click buffers a draft start (no onChange yet, but visually selected)', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 1, 12, 0, 0)); // July 2026, so start=null opens on July.
    const onChange = vi.fn();
    render(
      <DateRangePopover anchorEl={anchor} start={null} end={null} onChange={onChange} onClose={vi.fn()} />,
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
      <DateRangePopover anchorEl={anchor} start={null} end={null} onChange={onChange} onClose={vi.fn()} />,
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
      <DateRangePopover
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
      <DateRangePopover
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
      <DateRangePopover
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
      <DateRangePopover
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
      <DateRangePopover
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
      <DateRangePopover
        anchorEl={anchor}
        start="2026-07-05"
        end="2026-07-10"
        onChange={onChange}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('date-range-clear'));
    expect(onChange).toHaveBeenLastCalledWith(null, null);
    expect(screen.getByTestId('date-range-start-cell')).not.toHaveTextContent('2026-07-05');
  });

  it('Escape closes the popover', () => {
    const onClose = vi.fn();
    render(
      <DateRangePopover anchorEl={anchor} start={null} end={null} onChange={vi.fn()} onClose={onClose} />,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('an outside click closes the popover, but an inside click does not', async () => {
    const onClose = vi.fn();
    render(
      <DateRangePopover anchorEl={anchor} start={null} end={null} onChange={vi.fn()} onClose={onClose} />,
    );
    // The listener is attached via a deferred setTimeout(0) to dodge the
    // opening click; flush it first.
    await new Promise((r) => setTimeout(r, 0));

    const inside = screen.getByTestId('date-range-clear');
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
      <DateRangePopover anchorEl={anchor} start={null} end={null} onChange={vi.fn()} onClose={onClose} />,
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
      <DateRangePopover anchorEl={anchor} start={null} end={null} onChange={vi.fn()} onClose={vi.fn()} />,
    );
    const pop = screen.getByTestId('date-range-popover');
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
      <DateRangePopover anchorEl={anchor} start={null} end={null} onChange={vi.fn()} onClose={vi.fn()} />,
    );
    const pop = screen.getByTestId('date-range-popover');
    Object.defineProperty(pop, 'offsetHeight', { configurable: true, value: 300 });
    fireEvent(window, new Event('resize'));

    expect(parseFloat(pop.style.top)).toBe(120 + 6);
  });
});
