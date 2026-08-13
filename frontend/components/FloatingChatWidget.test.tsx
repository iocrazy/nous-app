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
