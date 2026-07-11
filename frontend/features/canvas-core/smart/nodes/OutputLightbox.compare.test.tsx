// features/canvas-core/smart/nodes/OutputLightbox.compare.test.tsx
// Compare divider (P1-4, Infinite parity): the split line itself is the
// drag target (30px hit zone + grip knob, pointer-captured), replacing the
// old bottom range control; multiple compare sources render as thumbnail
// pickers (upstream input images), replacing the single hardwired source.

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { OutputLightbox } from './OutputLightbox';

const ITEMS = [
  { url: '/gm/1/cover', name: 'first.png' },
  { url: '/gm/2/cover', name: 'second.png' },
];

const SOURCES = [
  { url: '/gm/10/cover', name: 'input-a.png' },
  { url: '/gm/11/cover', name: 'input-b.png' },
];

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderBox(overrides: Partial<Parameters<typeof OutputLightbox>[0]> = {}) {
  const onClose = vi.fn();
  const onIndexChange = vi.fn();
  const utils = render(
    <OutputLightbox
      items={ITEMS}
      index={0}
      kind="image"
      onIndexChange={onIndexChange}
      onClose={onClose}
      compareSources={[SOURCES[0]]}
      {...overrides}
    />,
  );
  return { onClose, onIndexChange, ...utils };
}

function openCompare() {
  fireEvent.click(screen.getByRole('button', { name: 'Compare' }));
}

/** jsdom rects are all zeros — pin the compare stage to a 200px-wide box so
 *  clientX → percent math is deterministic. */
function mockStageRect() {
  const stage = screen.getByTestId('compare-stage');
  vi.spyOn(stage, 'getBoundingClientRect').mockReturnValue({
    left: 0,
    top: 0,
    right: 200,
    bottom: 100,
    width: 200,
    height: 100,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect);
}

describe('compare divider drag (P1-4)', () => {
  it('renders a draggable divider instead of the old range control', () => {
    renderBox();
    openCompare();
    const divider = screen.getByTestId('compare-divider');
    expect(divider.getAttribute('role')).toBe('slider');
    // The bottom <input type=range> is gone.
    expect(document.querySelector('input[type="range"]')).toBeNull();
  });

  it('pointer-dragging the divider moves the clip split', () => {
    renderBox();
    openCompare();
    mockStageRect();
    const divider = screen.getByTestId('compare-divider');
    fireEvent.pointerDown(divider, { pointerId: 1, clientX: 100, button: 0 });
    fireEvent.pointerMove(divider, { pointerId: 1, clientX: 50 });
    fireEvent.pointerUp(divider, { pointerId: 1 });
    const result = screen.getByTestId('compare-result');
    // 50 / 200 = 25% → result clipped to inset(0 75% 0 0).
    expect(result.style.clipPath).toContain('75%');
    expect(screen.getByTestId('compare-divider').style.left).toBe('25%');
  });

  it('arrow keys nudge the divider and do not switch lightbox images', () => {
    const { onIndexChange } = renderBox();
    openCompare();
    const divider = screen.getByTestId('compare-divider');
    fireEvent.keyDown(divider, { key: 'ArrowLeft' });
    const result = screen.getByTestId('compare-result');
    expect(result.style.clipPath).not.toContain('50%');
    expect(onIndexChange).not.toHaveBeenCalled();
  });
});

describe('compare source picker (P1-4)', () => {
  it('a single source shows no thumbnail row', () => {
    renderBox();
    openCompare();
    expect(screen.queryByTestId('compare-thumbs')).toBeNull();
  });

  it('multiple sources render thumbnails; clicking one swaps the underlay', () => {
    renderBox({ compareSources: SOURCES });
    openCompare();
    const thumbs = screen.getByTestId('compare-thumbs');
    expect(thumbs.querySelectorAll('img').length).toBe(2);
    expect(screen.getByTestId('compare-original')).toHaveProperty(
      'src',
      expect.stringContaining('/gm/10/cover') as unknown as string,
    );
    fireEvent.click(screen.getByTestId('compare-thumb-1'));
    expect(screen.getByTestId('compare-original')).toHaveProperty(
      'src',
      expect.stringContaining('/gm/11/cover') as unknown as string,
    );
  });

  it('no sources → no Compare button at all', () => {
    renderBox({ compareSources: [] });
    expect(screen.queryByRole('button', { name: 'Compare' })).toBeNull();
  });
});
