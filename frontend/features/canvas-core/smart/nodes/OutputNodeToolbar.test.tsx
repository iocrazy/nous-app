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
      <OutputNodeToolbar items={ITEMS} onPreview={onPreview} onRerun={onRerun} pinned hovered={false} />,
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
      <OutputNodeToolbar items={ITEMS} onPreview={vi.fn()} pinned hovered={false} />,
    );
    expect(screen.queryByRole('button', { name: 'Rerun' })).toBeNull();
    rerender(
      <OutputNodeToolbar items={ITEMS} onPreview={vi.fn()} onRerun={vi.fn()} rerunning pinned hovered={false} />,
    );
    expect(
      (screen.getByRole('button', { name: 'Rerun' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('renders nothing with no items', () => {
    render(<OutputNodeToolbar items={[]} onPreview={vi.fn()} pinned hovered={false} />);
    expect(screen.queryByTestId('output-node-toolbar')).toBeNull();
  });

  it('every button is nodrag so clicks never start a node drag', () => {
    render(<OutputNodeToolbar items={ITEMS} onPreview={vi.fn()} onRerun={vi.fn()} pinned hovered={false} />);
    const buttons = screen
      .getByTestId('output-node-toolbar')
      .querySelectorAll('button');
    expect(buttons.length).toBe(3);
    buttons.forEach((b) => expect(b.className).toContain('nodrag'));
  });
});

it('renders a Brush key that opens the brush editor when wired', () => {
  const onBrush = vi.fn();
  render(
    <OutputNodeToolbar
      items={[{ url: '/gm/1/file', name: 'a.png' }]}
      onPreview={() => {}}
      onBrush={onBrush}
      pinned
      hovered={false}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Brush' }));
  expect(onBrush).toHaveBeenCalled();
});

describe('OutputNodeToolbar — mounted only when it can be used (fluency T5)', () => {
  // Before this task the bar was always in the DOM and merely faded with
  // `opacity-0`. A hidden-but-present frosted island still costs the
  // compositor a blur + shadow on every node, every frame of every pan.
  it('is absent from the DOM until hovered or pinned', () => {
    const { rerender } = render(
      <OutputNodeToolbar items={ITEMS} onPreview={vi.fn()} pinned={false} hovered={false} />,
    );
    expect(screen.queryByTestId('output-node-toolbar')).toBeNull();
    rerender(
      <OutputNodeToolbar items={ITEMS} onPreview={vi.fn()} pinned={false} hovered />,
    );
    expect(screen.getByTestId('output-node-toolbar')).toBeInTheDocument();
  });

  it('mounts while pinned even with no pointer over the node', () => {
    render(
      <OutputNodeToolbar items={ITEMS} onPreview={vi.fn()} pinned hovered={false} />,
    );
    expect(screen.getByTestId('output-node-toolbar')).toBeInTheDocument();
  });

  it('is VISIBLE when hover revealed it, not merely mounted at opacity-0', () => {
    // The mount gate and the opacity class have to agree. `group-hover` is
    // the CSS :hover, which is NOT the hook's focus-within state — so a bar
    // revealed by focus alone used to compute to opacity-0: a focused
    // control the keyboard user cannot see.
    render(
      <OutputNodeToolbar items={ITEMS} onPreview={vi.fn()} pinned={false} hovered />,
    );
    const bar = screen.getByTestId('output-node-toolbar');
    expect(bar.classList.contains('opacity-100')).toBe(true);
    expect(bar.classList.contains('opacity-0')).toBe(false);
  });
});
