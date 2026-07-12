// features/canvas-core/smart/nodes/OutputNodeToolbar.test.tsx
// Floating node toolbar (P2-3 small version — Infinite's
// smartNodeToolbarHtml, three keys): Preview opens the lightbox with zero
// delay, Download saves the current first item, Rerun re-runs the source
// prompt when one exists.

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const downloadUrl = vi.fn();
vi.mock('../downloadMedia', () => ({
  downloadUrl: (...a: unknown[]) => downloadUrl(...a),
  downloadName: (item: { name?: string }) => item.name ?? 'x',
}));

import { OutputNodeToolbar } from './OutputNodeToolbar';

const ITEMS = [{ url: '/gm/1/cover', name: 'first.png' }];

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('OutputNodeToolbar', () => {
  it('renders Preview / Download / Rerun and wires the callbacks', () => {
    const onPreview = vi.fn();
    const onRerun = vi.fn();
    render(
      <OutputNodeToolbar items={ITEMS} onPreview={onPreview} onRerun={onRerun} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(onPreview).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect(downloadUrl).toHaveBeenCalledWith(ITEMS[0], 0);
    fireEvent.click(screen.getByRole('button', { name: 'Rerun' }));
    expect(onRerun).toHaveBeenCalled();
  });

  it('hides Rerun without a source prompt and disables it while rerunning', () => {
    const { rerender } = render(
      <OutputNodeToolbar items={ITEMS} onPreview={vi.fn()} />,
    );
    expect(screen.queryByRole('button', { name: 'Rerun' })).toBeNull();
    rerender(
      <OutputNodeToolbar items={ITEMS} onPreview={vi.fn()} onRerun={vi.fn()} rerunning />,
    );
    expect(
      (screen.getByRole('button', { name: 'Rerun' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('renders nothing with no items', () => {
    render(<OutputNodeToolbar items={[]} onPreview={vi.fn()} />);
    expect(screen.queryByTestId('output-node-toolbar')).toBeNull();
  });

  it('every button is nodrag so clicks never start a node drag', () => {
    render(<OutputNodeToolbar items={ITEMS} onPreview={vi.fn()} onRerun={vi.fn()} />);
    const buttons = screen
      .getByTestId('output-node-toolbar')
      .querySelectorAll('button');
    expect(buttons.length).toBe(3);
    buttons.forEach((b) => expect(b.className).toContain('nodrag'));
  });
});
