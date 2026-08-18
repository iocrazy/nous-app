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
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('./AIChatPanel', () => ({
  AIChatPanel: () => <div data-testid="chat-panel" />,
}));

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

  it('collapses to the FAB when closed', () => {
    useGlobalChatStore.setState({ open: false });
    renderWidget();
    expect(screen.getByTestId('sb-toggle-chat')).toBeVisible();
    expect(screen.queryByTestId('chat-drag-handle')).toBeNull();
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
