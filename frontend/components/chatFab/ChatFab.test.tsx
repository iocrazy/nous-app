/**
 * ChatFab — the collapsed entry of the global AI chat (無我 mascot).
 *
 * Three behaviours are pinned here because each one has a cheap way to go
 * wrong silently: a drag that also opens the panel, a snap that lands on
 * the wrong edge or under the TopBar, and motion that keeps running for a
 * user who asked for none.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { ChatFab } from './ChatFab';
import { FAB_GUTTER_PX, FAB_SIZE_PX, TOP_CHROME_PX } from './fabGeometry';
import { useGlobalChatStore } from '../../stores/globalChatStore';

const VW = 1024;
const VH = 768;

function setViewport(w: number, h: number) {
  Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
  Object.defineProperty(window, 'innerHeight', { value: h, configurable: true });
}

function fab() {
  return screen.getByTestId('sb-toggle-chat');
}

/** Press on the fab, move the pointer by (dx, dy) in one or more steps, release. */
function gesture(steps: Array<[number, number]>, start = { x: 900, y: 600 }) {
  fireEvent.pointerDown(fab(), { clientX: start.x, clientY: start.y, button: 0 });
  for (const [dx, dy] of steps) {
    fireEvent.pointerMove(window, { clientX: start.x + dx, clientY: start.y + dy });
  }
  const last = steps[steps.length - 1] ?? [0, 0];
  fireEvent.pointerUp(window, { clientX: start.x + last[0], clientY: start.y + last[1] });
}

let matchMediaSpy: ReturnType<typeof vi.fn> | null = null;
function prefersReducedMotion(on: boolean) {
  matchMediaSpy = vi.fn((query: string) => ({
    matches: on && query.includes('prefers-reduced-motion'),
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
  Object.defineProperty(window, 'matchMedia', { value: matchMediaSpy, configurable: true, writable: true });
}

beforeEach(() => {
  setViewport(VW, VH);
  prefersReducedMotion(false);
  useGlobalChatStore.setState({
    open: false,
    fabSide: 'right',
    fabTop: null,
    fabActivity: 'idle',
    fabUnread: 0,
  });
});

const originalAnimate = Object.getOwnPropertyDescriptor(Element.prototype, 'animate');
afterEach(() => {
  vi.restoreAllMocks();
  if (originalAnimate) Object.defineProperty(Element.prototype, 'animate', originalAnimate);
  else delete (Element.prototype as unknown as { animate?: unknown }).animate;
});

describe('ChatFab · resting position', () => {
  it('docks on the right edge at the default height when nothing is remembered', () => {
    render(<ChatFab />);
    const el = fab();
    expect(el).toBeVisible();
    expect(el.dataset.side).toBe('right');
    expect(el.style.left).toBe(`${VW - FAB_SIZE_PX - FAB_GUTTER_PX}px`);
    expect(el.style.top).toBe(`${VH - FAB_SIZE_PX - 80}px`);
  });

  it('restores a remembered side and top', () => {
    useGlobalChatStore.setState({ fabSide: 'left', fabTop: 300 });
    render(<ChatFab />);
    expect(fab().dataset.side).toBe('left');
    expect(fab().style.left).toBe(`${FAB_GUTTER_PX}px`);
    expect(fab().style.top).toBe('300px');
  });

  it('clamps a remembered top on render without overwriting what the user chose', () => {
    useGlobalChatStore.setState({ fabSide: 'right', fabTop: 2000 });
    render(<ChatFab />);
    expect(fab().style.top).toBe(`${VH - FAB_SIZE_PX - FAB_GUTTER_PX}px`);
    // A short window (mobile keyboard, a temporary resize) must not erase the spot.
    expect(useGlobalChatStore.getState().fabTop).toBe(2000);
  });

  it('re-clamps when the window shrinks and keeps the remembered top', () => {
    useGlobalChatStore.setState({ fabSide: 'right', fabTop: 700 });
    render(<ChatFab />);
    setViewport(800, 500);
    fireEvent(window, new Event('resize'));
    expect(fab().style.top).toBe(`${500 - FAB_SIZE_PX - FAB_GUTTER_PX}px`);
    expect(fab().style.left).toBe(`${800 - FAB_SIZE_PX - FAB_GUTTER_PX}px`);
    expect(useGlobalChatStore.getState().fabTop).toBe(700);
    setViewport(VW, VH);
    fireEvent(window, new Event('resize'));
    expect(fab().style.top).toBe('700px');
  });
});

describe('ChatFab · drag', () => {
  it('follows the pointer while dragging and does not open the chat', () => {
    render(<ChatFab />);
    fireEvent.pointerDown(fab(), { clientX: 900, clientY: 600, button: 0 });
    fireEvent.pointerMove(window, { clientX: 700, clientY: 400 });
    // Started at left = VW-64 = 960, top = VH-56-80 = 632; moved by (-200, -200).
    expect(fab().dataset.phase).toBe('dragging');
    expect(fab().style.left).toBe('760px');
    expect(fab().style.top).toBe('432px');
    fireEvent.pointerUp(window, { clientX: 700, clientY: 400 });
    expect(useGlobalChatStore.getState().open).toBe(false);
  });

  it('never lets the top edge enter the TopBar band', () => {
    render(<ChatFab />);
    fireEvent.pointerDown(fab(), { clientX: 900, clientY: 600, button: 0 });
    fireEvent.pointerMove(window, { clientX: 900, clientY: -500 });
    expect(fab().style.top).toBe(`${TOP_CHROME_PX}px`);
    fireEvent.pointerUp(window, { clientX: 900, clientY: -500 });
    expect(useGlobalChatStore.getState().fabTop).toBe(TOP_CHROME_PX);
  });

  it('never leaves the viewport on the bottom or the sides while dragging', () => {
    render(<ChatFab />);
    fireEvent.pointerDown(fab(), { clientX: 900, clientY: 600, button: 0 });
    fireEvent.pointerMove(window, { clientX: 5000, clientY: 5000 });
    expect(fab().style.left).toBe(`${VW - FAB_SIZE_PX - FAB_GUTTER_PX}px`);
    expect(fab().style.top).toBe(`${VH - FAB_SIZE_PX - FAB_GUTTER_PX}px`);
    fireEvent.pointerMove(window, { clientX: -5000, clientY: 600 });
    expect(fab().style.left).toBe(`${FAB_GUTTER_PX}px`);
    fireEvent.pointerUp(window, { clientX: -5000, clientY: 600 });
  });

  it('ignores secondary-button presses', () => {
    render(<ChatFab />);
    fireEvent.pointerDown(fab(), { clientX: 900, clientY: 600, button: 2 });
    fireEvent.pointerMove(window, { clientX: 700, clientY: 400 });
    expect(fab().dataset.phase).not.toBe('dragging');
    fireEvent.pointerUp(window, { clientX: 700, clientY: 400 });
    expect(useGlobalChatStore.getState().open).toBe(false);
  });
});

describe('ChatFab · cancelled and orphaned gestures', () => {
  it('does not open the chat when the system cancels a press (palm, second finger, OS gesture)', () => {
    render(<ChatFab />);
    fireEvent.pointerDown(fab(), { clientX: 900, clientY: 600, button: 0 });
    fireEvent.pointerCancel(window, { clientX: 900, clientY: 600 });
    expect(useGlobalChatStore.getState().open).toBe(false);
    expect(fab().dataset.phase).toBe('docked');
  });

  it('snaps a cancelled drag where it was, without opening', () => {
    render(<ChatFab />);
    fireEvent.pointerDown(fab(), { clientX: 900, clientY: 600, button: 0 });
    fireEvent.pointerMove(window, { clientX: 200, clientY: 300 });
    fireEvent.pointerCancel(window, { clientX: 200, clientY: 300 });
    const s = useGlobalChatStore.getState();
    expect(s.open).toBe(false);
    expect(s.fabSide).toBe('left');
    expect(s.fabTop).toBe(VH - FAB_SIZE_PX - 80 - 300);
  });

  it('tears the drag down on unmount so a late pointerup changes nothing', () => {
    const view = render(<ChatFab />);
    fireEvent.pointerDown(fab(), { clientX: 900, clientY: 600, button: 0 });
    fireEvent.pointerMove(window, { clientX: 200, clientY: 300 });
    view.unmount();
    expect(() => fireEvent.pointerUp(window, { clientX: 200, clientY: 300 })).not.toThrow();
    const s = useGlobalChatStore.getState();
    expect(s.open).toBe(false);
    expect(s.fabSide).toBe('right');
    expect(s.fabTop).toBeNull();
  });

  it('a click that unmounts the fab mid-gesture leaves no window listeners behind', () => {
    const add = vi.spyOn(window, 'addEventListener');
    const remove = vi.spyOn(window, 'removeEventListener');
    const view = render(<ChatFab />);
    fireEvent.pointerDown(fab(), { clientX: 900, clientY: 600, button: 0 });
    view.unmount();
    for (const type of ['pointermove', 'pointerup', 'pointercancel']) {
      const added = add.mock.calls.filter(([t]) => t === type).map(([, fn]) => fn);
      const removed = remove.mock.calls.filter(([t]) => t === type).map(([, fn]) => fn);
      expect(added.length).toBeGreaterThan(0);
      for (const fn of added) expect(removed).toContain(fn);
    }
  });
});

describe('ChatFab · snap on release', () => {
  it('snaps to the left edge when released in the left half and remembers it', () => {
    render(<ChatFab />);
    gesture([[-700, -300]]); // centre ends near x=228 → left half
    const s = useGlobalChatStore.getState();
    expect(s.fabSide).toBe('left');
    expect(s.fabTop).toBe(VH - FAB_SIZE_PX - 80 - 300);
    expect(fab().dataset.side).toBe('left');
    expect(fab().style.left).toBe(`${FAB_GUTTER_PX}px`);
  });

  it('snaps back to the right edge when released in the right half', () => {
    useGlobalChatStore.setState({ fabSide: 'left', fabTop: 300 });
    render(<ChatFab />);
    gesture([[600, 50]], { x: 30, y: 320 }); // from the left edge to x≈630 → right half
    expect(useGlobalChatStore.getState().fabSide).toBe('right');
    expect(useGlobalChatStore.getState().fabTop).toBe(350);
    expect(fab().style.left).toBe(`${VW - FAB_SIZE_PX - FAB_GUTTER_PX}px`);
  });

  it('keeps the drop height, clamped, as the new resting top', () => {
    render(<ChatFab />);
    gesture([[-100, -10_000]]);
    expect(useGlobalChatStore.getState().fabTop).toBe(TOP_CHROME_PX);
    expect(fab().style.top).toBe(`${TOP_CHROME_PX}px`);
  });
});

describe('ChatFab · click vs drag', () => {
  it('opens the chat on a press-and-release without movement', () => {
    render(<ChatFab />);
    gesture([]);
    expect(useGlobalChatStore.getState().open).toBe(true);
  });

  it('still counts as a click when the pointer drifted under 4px', () => {
    render(<ChatFab />);
    gesture([[2, 2]]);
    expect(useGlobalChatStore.getState().open).toBe(true);
    expect(useGlobalChatStore.getState().fabSide).toBe('right');
  });

  it('does not open once the pointer moved 4px or more, even if it comes back', () => {
    render(<ChatFab />);
    gesture([[5, 0], [0, 0]]);
    expect(useGlobalChatStore.getState().open).toBe(false);
  });

  it('opens from the keyboard with Enter and Space', () => {
    render(<ChatFab />);
    fireEvent.keyDown(fab(), { key: 'Enter' });
    expect(useGlobalChatStore.getState().open).toBe(true);
    useGlobalChatStore.setState({ open: false });
    fireEvent.keyDown(fab(), { key: ' ' });
    expect(useGlobalChatStore.getState().open).toBe(true);
  });

  it('keeps the shared test id, title and label the rest of the app relies on', () => {
    render(<ChatFab />);
    expect(fab().getAttribute('title')).toBe('AI Chat (⌘I)');
    expect(fab().getAttribute('aria-label')).toBe('Open AI Chat');
  });
});

describe('ChatFab · chat state on the mascot', () => {
  it('shows the unread badge and says so in the label', () => {
    useGlobalChatStore.setState({ fabUnread: 2 });
    render(<ChatFab />);
    expect(screen.getByTestId('sb-chat-unread')).toHaveTextContent('2');
    expect(fab().getAttribute('aria-label')).toBe('Open AI Chat, 2 new replies');
    expect(fab().dataset.mood).toContain('excited');
    expect(fab().dataset.face).toBe('excited');
  });

  it('rests with the serene face and opens its eyes when the cursor comes near', () => {
    render(<ChatFab />);
    expect(fab().dataset.face).toBe('serene');
    // Far away: nothing. Within 110px of the centre: attentive.
    fireEvent.pointerMove(document, { clientX: 100, clientY: 100 });
    expect(fab().dataset.face).toBe('serene');
    const el = fab();
    el.getBoundingClientRect = () => ({ left: 960, top: 632, width: 56, height: 56, right: 1016, bottom: 688, x: 960, y: 632, toJSON: () => ({}) });
    fireEvent.pointerMove(document, { clientX: 1000, clientY: 600 });
    expect(fab().dataset.face).toBe('open');
    expect(fab().dataset.mood).toContain('attentive');
  });

  it('grins while hovered and while being dragged shows wide-open eyes', () => {
    render(<ChatFab />);
    fireEvent.pointerEnter(fab());
    expect(fab().dataset.face).toBe('happy');
    fireEvent.pointerLeave(fab());
    fireEvent.pointerDown(fab(), { clientX: 900, clientY: 600, button: 0 });
    fireEvent.pointerMove(window, { clientX: 850, clientY: 550 });
    expect(fab().dataset.face).toBe('open');
    fireEvent.pointerUp(window, { clientX: 850, clientY: 550 });
  });

  it('hides the badge at zero', () => {
    render(<ChatFab />);
    expect(screen.queryByTestId('sb-chat-unread')).toBeNull();
  });

  it('wears the focused face while a run is in flight', () => {
    useGlobalChatStore.setState({ fabActivity: 'running' });
    render(<ChatFab />);
    expect(fab().dataset.mood).toContain('working');
    expect(fab().dataset.face).toBe('working');
    expect(fab().getAttribute('aria-label')).toBe('Open AI Chat, agent working');
  });

  it('wears the thinking face while the agent waits for an answer', () => {
    useGlobalChatStore.setState({ fabActivity: 'waiting' });
    render(<ChatFab />);
    expect(fab().dataset.mood).toContain('thinking');
    expect(fab().dataset.face).toBe('thinking');
    expect(fab().getAttribute('aria-label')).toBe('Open AI Chat, waiting for your answer');
  });
});

describe('ChatFab · prefers-reduced-motion', () => {
  it('marks itself motion-off and never calls the Web Animations API', () => {
    prefersReducedMotion(true);
    const animate = vi.fn();
    Object.defineProperty(Element.prototype, 'animate', { value: animate, configurable: true, writable: true });
    render(<ChatFab />);
    expect(fab().dataset.motion).toBe('off');
    gesture([[-700, -300]]); // a snap normally squashes on landing
    gesture([]); // a click normally pops before opening
    expect(animate).not.toHaveBeenCalled();
  });

  it('runs the one-off animations when motion is allowed', () => {
    const animate = vi.fn(() => ({ finished: Promise.resolve() }));
    Object.defineProperty(Element.prototype, 'animate', { value: animate, configurable: true, writable: true });
    render(<ChatFab />);
    expect(fab().dataset.motion).toBe('on');
    gesture([]);
    expect(animate).toHaveBeenCalled();
  });
});
