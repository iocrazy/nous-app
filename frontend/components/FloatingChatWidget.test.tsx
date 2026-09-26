/**
 * FloatingChatWidget — drag discoverability (spec F4).
 *
 * The window has always been draggable by its title bar, but the bar was
 * 32px tall and packed with buttons (which `onDragStart` deliberately skips
 * via `closest('button')`), so the actual grab area was a few pixels wide
 * with nothing indicating it existed — users reported the window as
 * "floating, can't be moved". These tests pin the explicit handle AND the
 * property that makes it work: it must not be a button, or the drag guard
 * would exclude it.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// The mock panel counts its mounts and, like the real one, mirrors a live
// turn onto the mascot — so a remount (or an unmount on collapse) shows up
// both as a second mount and as `running` dropping to `idle`.
const panel = vi.hoisted(() => ({
  mounts: 0,
  sending: false,
  lastProps: null as Record<string, unknown> | null,
}));
vi.mock('./AIChatPanel', async () => {
  const { useEffect } = await import('react');
  const { useFabActivityPublisher } = await import('./chatFab/useFabActivityPublisher');
  return {
    AIChatPanel: (props: Record<string, unknown>) => {
      panel.lastProps = props;
      useEffect(() => {
        panel.mounts += 1;
      }, []);
      useFabActivityPublisher(panel.sending, false);
      return <div data-testid="chat-panel" />;
    },
  };
});

import { FloatingChatWidget } from './FloatingChatWidget';
import { useGlobalChatStore } from '../stores/globalChatStore';

function renderWidget() {
  return render(
    <MemoryRouter initialEntries={['/library']}>
      <FloatingChatWidget />
    </MemoryRouter>,
  );
}

describe('FloatingChatWidget drag handle', () => {
  beforeEach(() => {
    useGlobalChatStore.setState({
      open: true,
      right: 16,
      bottom: 16,
      width: 400,
      height: 620,
      pageContext: null,
    });
  });

  it('shows an explicit grab handle in the title bar', () => {
    renderWidget();
    expect(screen.getByTestId('chat-drag-handle')).toBeVisible();
  });

  it('renders the handle as a non-button so the drag guard keeps it', () => {
    renderWidget();
    const handle = screen.getByTestId('chat-drag-handle');
    // `onDragStart` returns early for anything inside a <button> — a button
    // handle would look right and never drag.
    expect(handle.closest('button')).toBeNull();
  });

  it('moves the window when dragged from the handle', () => {
    renderWidget();
    const handle = screen.getByTestId('chat-drag-handle');

    fireEvent.pointerDown(handle, { clientX: 300, clientY: 300 });
    fireEvent.pointerMove(window, { clientX: 200, clientY: 250 });
    fireEvent.pointerUp(window);

    // Anchored bottom-right: dragging left/up grows both offsets.
    expect(useGlobalChatStore.getState().right).toBe(116);
    expect(useGlobalChatStore.getState().bottom).toBe(66);
  });

  it('does not drag when the pointer goes down on a title-bar button', () => {
    renderWidget();

    fireEvent.pointerDown(screen.getByLabelText('Minimize AI Chat'), {
      clientX: 300,
      clientY: 300,
    });
    fireEvent.pointerMove(window, { clientX: 200, clientY: 250 });
    fireEvent.pointerUp(window);

    expect(useGlobalChatStore.getState().right).toBe(16);
    expect(useGlobalChatStore.getState().bottom).toBe(16);
  });

  it('collapses to the FAB when closed, keeping the window mounted but hidden', () => {
    useGlobalChatStore.setState({ open: false });
    renderWidget();
    expect(screen.getByTestId('sb-toggle-chat')).toBeVisible();
    const win = screen.getByTestId('sb-panel-chat');
    expect(win).toBeInTheDocument();
    expect(win).not.toBeVisible();
    expect(win).toHaveAttribute('aria-hidden', 'true');
    expect(panel.lastProps?.collapsed).toBe(true);
  });
});

describe('FloatingChatWidget keeps the panel mounted across collapse', () => {
  beforeEach(() => {
    panel.mounts = 0;
    panel.sending = false;
    panel.lastProps = null;
    useGlobalChatStore.setState({
      open: true,
      right: 16,
      bottom: 16,
      width: 400,
      height: 620,
      pageContext: null,
      fabActivity: 'idle',
      fabUnread: 0,
    });
  });

  it('collapse then expand reuses the same panel instance', () => {
    renderWidget();
    const node = screen.getByTestId('chat-panel');
    expect(panel.lastProps?.collapsed).toBe(false);

    act(() => useGlobalChatStore.getState().setOpen(false));
    expect(screen.getByTestId('chat-panel')).toBe(node);
    expect(screen.getByTestId('sb-toggle-chat')).toBeVisible();

    act(() => useGlobalChatStore.getState().setOpen(true));
    expect(screen.getByTestId('chat-panel')).toBe(node);
    expect(screen.getByTestId('sb-panel-chat')).toBeVisible();
    expect(screen.queryByTestId('sb-toggle-chat')).toBeNull();
    expect(panel.mounts).toBe(1);
  });

  it('keeps the mascot running when the window is collapsed mid-turn', () => {
    panel.sending = true;
    renderWidget();
    expect(useGlobalChatStore.getState().fabActivity).toBe('running');

    act(() => useGlobalChatStore.getState().setOpen(false));
    expect(useGlobalChatStore.getState().fabActivity).toBe('running');
  });

  it('ESC does nothing while collapsed', () => {
    renderWidget();
    act(() => useGlobalChatStore.getState().setOpen(false));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(useGlobalChatStore.getState().open).toBe(false);
  });

  it('still unmounts the panel on the Chat page', () => {
    panel.sending = true;
    render(
      <MemoryRouter initialEntries={['/team/1/chat']}>
        <FloatingChatWidget />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId('chat-panel')).toBeNull();
    expect(screen.queryByTestId('sb-toggle-chat')).toBeNull();
  });
});

describe('FloatingChatWidget top-chrome clamp', () => {
  // The TopBar (48px, z-50) sits ABOVE the widget (z-40): if the title bar
  // slides underneath it, pointerdown lands on the TopBar and the window
  // can never be grabbed again. These tests pin the invariant that the top
  // edge (innerHeight - bottom - height) never enters that band.
  beforeEach(() => {
    Object.defineProperty(window, 'innerWidth', { value: 1024, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 768, configurable: true });
    useGlobalChatStore.setState({
      open: true,
      right: 16,
      bottom: 16,
      width: 400,
      height: 620,
      pageContext: null,
    });
  });

  it('stops a drag before the title bar slides under the TopBar', () => {
    renderWidget();
    const handle = screen.getByTestId('chat-drag-handle');
    fireEvent.pointerDown(handle, { clientX: 500, clientY: 500 });
    fireEvent.pointerMove(window, { clientX: 500, clientY: 0 });
    fireEvent.pointerUp(window);
    const s = useGlobalChatStore.getState();
    const top = 768 - s.bottom - s.height;
    // 768 - 620 - 56 = 92: the drag must clamp exactly at the chrome band.
    expect(s.bottom).toBe(92);
    expect(top).toBeGreaterThanOrEqual(56);
  });

  it('self-heals a persisted rect that is already stuck under the TopBar', () => {
    // localStorage from before this fix (or from a taller browser window)
    // can restore bottom so large the title bar is unreachable. fit() on
    // mount must pull it back out.
    useGlobalChatStore.setState({ bottom: 700 });
    renderWidget();
    const s = useGlobalChatStore.getState();
    expect(768 - s.bottom - s.height).toBeGreaterThanOrEqual(56);
  });
});
