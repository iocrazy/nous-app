// features/canvas-core/ui/ShortcutHelpPanel.test.tsx
// Shortcut help overlay (? key): grouped shortcut list, Esc / backdrop /
// close-button dismissal, body portal.

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ShortcutHelpPanel } from './ShortcutHelpPanel';
import { CANVAS_SHORTCUT_GROUPS, formatKey } from './canvasShortcuts';

afterEach(cleanup);

describe('canvasShortcuts data', () => {
  it('every entry has keys and a label; groups are non-empty', () => {
    expect(CANVAS_SHORTCUT_GROUPS.length).toBeGreaterThan(0);
    for (const g of CANVAS_SHORTCUT_GROUPS) {
      expect(g.entries.length).toBeGreaterThan(0);
      for (const e of g.entries) {
        expect(e.keys.length).toBeGreaterThan(0);
        expect(e.label).toBeTruthy();
      }
    }
  });

  it('formatKey maps mod per platform', () => {
    expect(formatKey('mod', true)).toBe('⌘');
    expect(formatKey('mod', false)).toBe('Ctrl');
    expect(formatKey('K', false)).toBe('K');
  });
});

describe('ShortcutHelpPanel', () => {
  it('renders nothing when closed', () => {
    render(<ShortcutHelpPanel open={false} onClose={vi.fn()} />);
    expect(screen.queryByTestId('shortcut-help')).toBeNull();
  });

  it('lists every group title and shortcut label when open', () => {
    render(<ShortcutHelpPanel open onClose={vi.fn()} />);
    for (const g of CANVAS_SHORTCUT_GROUPS) {
      expect(screen.getByText(g.title)).toBeTruthy();
      for (const e of g.entries) {
        expect(screen.getByText(e.label)).toBeTruthy();
      }
    }
  });

  it('portals to <body>, not the render container', () => {
    const { container } = render(<ShortcutHelpPanel open onClose={vi.fn()} />);
    const panel = screen.getByTestId('shortcut-help');
    expect(container.contains(panel)).toBe(false);
    expect(panel.parentElement).toBe(document.body);
  });

  it('Escape, backdrop click, and the close button all dismiss', () => {
    const onClose = vi.fn();
    const { rerender } = render(<ShortcutHelpPanel open onClose={onClose} />);
    fireEvent.keyDown(screen.getByTestId('shortcut-help'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);

    rerender(<ShortcutHelpPanel open onClose={onClose} />);
    fireEvent.click(screen.getByTestId('shortcut-help'));
    expect(onClose).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(3);
  });
});
