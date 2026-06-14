/**
 * Tests for CommandPalette (Phase 6c).
 *
 * Covers: open/close rendering, search filtering, keyboard navigation
 * (↑↓ + Enter runs highlighted command), Esc closes, backdrop click closes.
 * Commands are injected via the `commands` prop to avoid store coupling.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CommandPalette } from './CommandPalette';

// ---- Fixtures ------------------------------------------------------------

const runA = vi.fn();
const runB = vi.fn();

const mockCommands = [
  { id: 'alpha', title: 'Alpha Action', hint: '⌘1', run: runA },
  { id: 'beta', title: 'Beta Action', hint: '⌘2', run: runB },
];

const onClose = vi.fn();

function renderPalette({ open = true }: { open?: boolean } = {}) {
  return render(
    <CommandPalette open={open} onClose={onClose} commands={mockCommands} />,
  );
}

// ---- Lifecycle -----------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.clearAllMocks();
});

// ---- Open / close --------------------------------------------------------

describe('CommandPalette — open / close', () => {
  it('renders nothing when closed', () => {
    renderPalette({ open: false });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('renders the dialog when open', () => {
    renderPalette();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('renders the search input when open', () => {
    renderPalette();
    expect(screen.getByPlaceholderText('Search commands…')).toBeInTheDocument();
  });

  it('clicking the backdrop calls onClose', () => {
    renderPalette();
    // The dialog element IS the backdrop wrapper
    fireEvent.click(screen.getByRole('dialog'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('clicking inside the panel does NOT call onClose', () => {
    renderPalette();
    // Click the search input (inside the panel) — stopPropagation prevents closing
    fireEvent.click(screen.getByPlaceholderText('Search commands…'));
    expect(onClose).not.toHaveBeenCalled();
  });
});

// ---- Command list --------------------------------------------------------

describe('CommandPalette — command list', () => {
  it('shows all commands when query is empty', () => {
    renderPalette();
    expect(screen.getByText('Alpha Action')).toBeInTheDocument();
    expect(screen.getByText('Beta Action')).toBeInTheDocument();
  });

  it('shows hint text alongside command title', () => {
    renderPalette();
    expect(screen.getByText('⌘1')).toBeInTheDocument();
  });

  it('filters commands as the user types', () => {
    renderPalette();
    const input = screen.getByPlaceholderText('Search commands…');
    fireEvent.change(input, { target: { value: 'alpha' } });
    expect(screen.getByText('Alpha Action')).toBeInTheDocument();
    expect(screen.queryByText('Beta Action')).toBeNull();
  });

  it('shows "No commands found" when nothing matches', () => {
    renderPalette();
    const input = screen.getByPlaceholderText('Search commands…');
    fireEvent.change(input, { target: { value: 'zzzzzz' } });
    expect(screen.getByText('No commands found')).toBeInTheDocument();
  });
});

// ---- Keyboard navigation -------------------------------------------------

describe('CommandPalette — keyboard navigation', () => {
  it('Esc calls onClose', () => {
    renderPalette();
    const input = screen.getByPlaceholderText('Search commands…');
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Enter runs the first command (highlighted by default) and closes', () => {
    renderPalette();
    const input = screen.getByPlaceholderText('Search commands…');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(runA).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(runB).not.toHaveBeenCalled();
  });

  it('ArrowDown moves highlight to the next item; Enter runs it', () => {
    renderPalette();
    const input = screen.getByPlaceholderText('Search commands…');
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(runB).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(runA).not.toHaveBeenCalled();
  });

  it('ArrowUp from first item wraps to the last item', () => {
    renderPalette();
    const input = screen.getByPlaceholderText('Search commands…');
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(runB).toHaveBeenCalledTimes(1); // beta is last
    expect(runA).not.toHaveBeenCalled();
  });

  it('Enter does not run a disabled command', () => {
    const disabledRun = vi.fn();
    const { rerender } = render(
      <CommandPalette
        open
        onClose={onClose}
        commands={[
          { id: 'disabled', title: 'Disabled', run: disabledRun, enabled: () => false },
        ]}
      />,
    );
    const input = screen.getByPlaceholderText('Search commands…');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(disabledRun).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    rerender(<></>); // cleanup
  });

  it('clicking a command item runs it and closes', () => {
    renderPalette();
    fireEvent.click(screen.getByText('Alpha Action'));
    expect(runA).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

// ---- State reset on re-open ----------------------------------------------

describe('CommandPalette — state reset', () => {
  it('resets query and highlight index when re-opened', () => {
    const { rerender } = renderPalette();
    const input = screen.getByPlaceholderText('Search commands…');

    // Type something and move highlight down
    fireEvent.change(input, { target: { value: 'beta' } });
    fireEvent.keyDown(input, { key: 'ArrowDown' });

    // Close then re-open
    rerender(
      <CommandPalette open={false} onClose={onClose} commands={mockCommands} />,
    );
    rerender(
      <CommandPalette open onClose={onClose} commands={mockCommands} />,
    );

    // After re-open the query should be reset and first item should be highlighted
    expect(screen.getByPlaceholderText('Search commands…')).toHaveValue('');
    // Both commands visible again (query cleared)
    expect(screen.getByText('Alpha Action')).toBeInTheDocument();
    expect(screen.getByText('Beta Action')).toBeInTheDocument();
  });
});
