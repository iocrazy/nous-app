// features/canvas-core/smart/WorkflowLibraryPicker.test.tsx
//
// Portal + fixed-positioning behavior (T8 review fix round 1): scroll-close
// must use the same capture-phase listener as DateTimePopover so a scroll
// of the composer's own `overflow-x-auto` toolbar (the anchor button's
// scrollable ancestor) closes the panel instead of leaving it floating with
// no anchor underneath it. Height-flip fallback mirrors
// DateTimePopover.test.tsx's paired "flips"/"places" tests, adapted for
// this panel's inverted default (opens upward, flips to below).
//
// Review fix round 2: round 1's capture-phase scroll listener was
// unconditional (`() => onClose()`), which also fires for scrolls of the
// panel's OWN internal file-row list (`max-h-56 overflow-y-auto` — this
// mock seeds enough rows to make that list actually scrollable). Unlike
// DateTimePopover (no internal scroll region), a bare `onClose()` there
// self-closes the panel the instant the user scrolls its own row list.
// Fixed with the same target-containment filter `onMouseDown` already uses.

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../hooks/useResourceSearch', () => ({
  useResourceSearch: () => ({
    data: {
      results: [
        { id: '88', name: 'wf-a.json', kind: 'doc' },
        { id: '89', name: 'wf-b.json', kind: 'doc' },
        { id: '90', name: 'wf-c.json', kind: 'doc' },
        { id: '91', name: 'wf-d.json', kind: 'doc' },
        { id: '92', name: 'wf-e.json', kind: 'doc' },
        { id: '93', name: 'wf-f.json', kind: 'doc' },
        { id: '94', name: 'wf-g.json', kind: 'doc' },
        { id: '95', name: 'wf-h.json', kind: 'doc' },
      ],
    },
    loading: false,
  }),
}));

import { WorkflowLibraryPicker } from './WorkflowLibraryPicker';

describe('WorkflowLibraryPicker', () => {
  let anchor: HTMLButtonElement;

  beforeEach(() => {
    anchor = document.createElement('button');
    document.body.appendChild(anchor);
  });

  afterEach(() => {
    cleanup();
    anchor.remove();
  });

  it('renders null (no portal) when anchorEl is null', () => {
    const { container } = render(
      <WorkflowLibraryPicker anchorEl={null} teamId="team-1" onPick={vi.fn()} onClose={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('workflow-library-picker')).toBeNull();
  });

  it('renders into document.body via portal, fixed-positioned, as an accessible dialog', () => {
    render(
      <WorkflowLibraryPicker anchorEl={anchor} teamId="team-1" onPick={vi.fn()} onClose={vi.fn()} />,
    );
    const pop = screen.getByTestId('workflow-library-picker');
    expect(pop.parentElement).toBe(document.body);
    expect(pop.style.position).toBe('fixed');
    expect(screen.getByRole('dialog', { name: 'Workflows' })).toBe(pop);
  });

  it('Escape closes the picker', () => {
    const onClose = vi.fn();
    render(
      <WorkflowLibraryPicker anchorEl={anchor} teamId="team-1" onPick={vi.fn()} onClose={onClose} />,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('an outside click closes the picker, but an inside click does not', async () => {
    const onClose = vi.fn();
    render(
      <WorkflowLibraryPicker anchorEl={anchor} teamId="team-1" onPick={vi.fn()} onClose={onClose} />,
    );
    // The listener is attached via a deferred setTimeout(0) to dodge the
    // opening click; flush it first.
    await new Promise((r) => setTimeout(r, 0));

    const inside = screen.getByRole('textbox');
    fireEvent.mouseDown(inside);
    expect(onClose).not.toHaveBeenCalled();

    const outside = document.createElement('div');
    document.body.appendChild(outside);
    fireEvent.mouseDown(outside);
    expect(onClose).toHaveBeenCalledTimes(1);
    outside.remove();
  });

  it('scrolling the window closes the picker', () => {
    const onClose = vi.fn();
    render(
      <WorkflowLibraryPicker anchorEl={anchor} teamId="team-1" onPick={vi.fn()} onClose={onClose} />,
    );
    fireEvent.scroll(window);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  // Review fix round 1 (Important): the original outside-click/Escape effect
  // was missing DateTimePopover's `window.addEventListener('scroll', ...,
  // true)` capture-phase registration. The anchor button lives inside the
  // composer's own `overflow-x-auto` toolbar — scrolling *that* container
  // (not the window) must still close the panel, or a narrow viewport with
  // many toolbar buttons leaves the fixed-position panel pinned in place
  // while the anchor scrolls out from under it. Capture-phase listeners on
  // `window` fire during the capturing phase for any scroll dispatched on a
  // descendant, independent of whether the scroll event itself bubbles —
  // that's the whole point of registering with `true`, verified here by
  // scrolling a nested container, not `window`.
  it('scrolling a nested scrollable ancestor of the anchor closes the picker (capture phase)', () => {
    const onClose = vi.fn();
    const scrollContainer = document.createElement('div');
    document.body.appendChild(scrollContainer);
    scrollContainer.appendChild(anchor); // anchor now lives inside a nested scrollable container
    render(
      <WorkflowLibraryPicker anchorEl={anchor} teamId="team-1" onPick={vi.fn()} onClose={onClose} />,
    );
    fireEvent.scroll(scrollContainer);
    expect(onClose).toHaveBeenCalledTimes(1);
    scrollContainer.remove();
  });

  // Review fix round 2: scrolling the panel's OWN internal row list must NOT
  // close it — only a scroll whose target is outside the panel means "the
  // anchor may have moved". The mock above seeds 8 rows so the
  // `max-h-56 overflow-y-auto` list is genuinely scrollable.
  it('scrolling the picker\'s own internal file-row list does not close it', () => {
    const onClose = vi.fn();
    render(
      <WorkflowLibraryPicker anchorEl={anchor} teamId="team-1" onPick={vi.fn()} onClose={onClose} />,
    );
    fireEvent.scroll(screen.getByTestId('workflow-library-rows'));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('places above the anchor by default when there is room', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 });
    anchor.getBoundingClientRect = () =>
      ({ top: 500, bottom: 520, left: 50, right: 150, width: 100, height: 20 }) as DOMRect;

    render(
      <WorkflowLibraryPicker anchorEl={anchor} teamId="team-1" onPick={vi.fn()} onClose={vi.fn()} />,
    );
    const pop = screen.getByTestId('workflow-library-picker');
    // jsdom always reports offsetHeight 0, so stub a real measured height to
    // exercise placement deterministically.
    Object.defineProperty(pop, 'offsetHeight', { configurable: true, value: 200 });
    fireEvent(window, new Event('resize'));

    // top = anchorTop(500) - 8 - h(200) = 292, comfortably >= 8 → no flip.
    expect(parseFloat(pop.style.top)).toBe(500 - 8 - 200);
  });

  // Review fix round 1 (Minor): pair the "places above" case with the flip
  // branch — same discipline as DateTimePopover.test.tsx's "flips
  // above"/"places below" pair, just mirrored for this panel's inverted
  // default direction (opens upward, flips to *below* when there's no room
  // above, vs. DateTimePopover which opens downward and flips *above*).
  it('flips below the anchor when there is no room above (measured height)', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 });
    anchor.getBoundingClientRect = () =>
      ({ top: 50, bottom: 70, left: 50, right: 150, width: 100, height: 20 }) as DOMRect;

    render(
      <WorkflowLibraryPicker anchorEl={anchor} teamId="team-1" onPick={vi.fn()} onClose={vi.fn()} />,
    );
    const pop = screen.getByTestId('workflow-library-picker');
    Object.defineProperty(pop, 'offsetHeight', { configurable: true, value: 320 });
    fireEvent(window, new Event('resize'));

    // above would be top(50)-8-h(320) = -278 < 8 → flip below: bottom(70)+8.
    const top = parseFloat(pop.style.top);
    expect(top).toBeGreaterThan(70); // below the anchor's bottom, not above its top
    expect(top).toBe(70 + 8);
  });

  it('clicking a row calls onPick with its resource id', () => {
    const onPick = vi.fn();
    render(
      <WorkflowLibraryPicker anchorEl={anchor} teamId="team-1" onPick={onPick} onClose={vi.fn()} />,
    );
    fireEvent.click(screen.getByText('wf-a.json'));
    expect(onPick).toHaveBeenCalledWith('88');
  });
});
