// features/canvas-core/smart/nodes/OutputLightbox.zoom.test.tsx
// Lightbox wheel-zoom + drag-pan (P0-5, Infinite parity): cursor-anchored
// wheel zoom on the stage, mouse-drag panning, double-click reset, and a
// zoom reset whenever the visible item changes.

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { OutputLightbox } from './OutputLightbox';

const ITEMS = [
  { url: '/gm/1/cover', name: 'first.png' },
  { url: '/gm/2/cover', name: 'second.png' },
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
      {...overrides}
    />,
  );
  return { onClose, onIndexChange, ...utils };
}

const scaleOf = (): number => {
  const stage = screen.getByTestId('lightbox-zoom-layer');
  const m = /scale\(([\d.]+)\)/.exec(stage.style.transform);
  return m ? Number(m[1]) : 1;
};

describe('OutputLightbox zoom & pan (P0-5)', () => {
  it('wheel-up zooms in, wheel-down zooms out, and shows a zoom readout', () => {
    renderBox();
    const stage = screen.getByTestId('lightbox-stage');
    fireEvent.wheel(stage, { deltaY: -120, clientX: 40, clientY: 40 });
    expect(scaleOf()).toBeGreaterThan(1);
    expect(screen.getByTestId('lightbox-zoom-readout').textContent).toMatch(/%/);
    fireEvent.wheel(stage, { deltaY: 120, clientX: 40, clientY: 40 });
    fireEvent.wheel(stage, { deltaY: 120, clientX: 40, clientY: 40 });
    expect(scaleOf()).toBeLessThan(1);
  });

  it('drag pans the zoom layer', () => {
    renderBox();
    const stage = screen.getByTestId('lightbox-stage');
    fireEvent.wheel(stage, { deltaY: -120, clientX: 10, clientY: 10 });
    const layer = screen.getByTestId('lightbox-zoom-layer');
    fireEvent.mouseDown(layer, { button: 0, clientX: 100, clientY: 100 });
    fireEvent.mouseMove(window, { clientX: 160, clientY: 130 });
    fireEvent.mouseUp(window);
    const t = screen.getByTestId('lightbox-zoom-layer').style.transform;
    const m = /translate\(([-\d.]+)px, ([-\d.]+)px\)/.exec(t);
    expect(m).toBeTruthy();
    expect(Number(m![1])).not.toBe(0);
  });

  it('double-click resets zoom and pan', () => {
    renderBox();
    const stage = screen.getByTestId('lightbox-stage');
    fireEvent.wheel(stage, { deltaY: -120, clientX: 40, clientY: 40 });
    expect(scaleOf()).toBeGreaterThan(1);
    fireEvent.doubleClick(screen.getByTestId('lightbox-zoom-layer'));
    expect(scaleOf()).toBe(1);
    expect(screen.queryByTestId('lightbox-zoom-readout')).toBeNull();
  });

  it('resets zoom when the item index changes', () => {
    const { rerender } = renderBox();
    const stage = screen.getByTestId('lightbox-stage');
    fireEvent.wheel(stage, { deltaY: -120, clientX: 40, clientY: 40 });
    expect(scaleOf()).toBeGreaterThan(1);
    rerender(
      <OutputLightbox
        items={ITEMS}
        index={1}
        kind="image"
        onIndexChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(scaleOf()).toBe(1);
  });

  it('does not zoom in compare mode', () => {
    renderBox({ compareSources: [{ url: '/gm/0/cover' }] });
    fireEvent.click(screen.getByRole('button', { name: 'Compare' }));
    const stage = screen.getByTestId('lightbox-stage');
    fireEvent.wheel(stage, { deltaY: -120, clientX: 40, clientY: 40 });
    expect(scaleOf()).toBe(1);
  });
});

describe('lightbox shell (P1-12)', () => {
  it('uses the theme-aware glass backdrop instead of flat black', () => {
    renderBox();
    const root = screen.getByTestId('output-lightbox');
    expect(root.className).toContain('mh-lightbox-backdrop');
    expect(root.className).not.toContain('bg-black');
  });
});

describe('progressive load (P1-3)', () => {
  it('shows a shimmer skeleton until the image loads', () => {
    renderBox();
    expect(screen.getByTestId('lightbox-skeleton')).toBeTruthy();
    fireEvent.load(screen.getByTestId('lightbox-image'));
    expect(screen.queryByTestId('lightbox-skeleton')).toBeNull();
  });

  it('broken image shows a recovery card whose Retry re-attempts the load', () => {
    renderBox();
    fireEvent.error(screen.getByTestId('lightbox-image'));
    expect(screen.getByTestId('lightbox-load-error')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    // Back to the skeleton + a fresh <img> attempt.
    expect(screen.getByTestId('lightbox-skeleton')).toBeTruthy();
    expect(screen.getByTestId('lightbox-image')).toBeTruthy();
  });

  it('switching items returns to the skeleton', () => {
    const { rerender } = renderBox();
    fireEvent.load(screen.getByTestId('lightbox-image'));
    rerender(
      <OutputLightbox
        items={ITEMS}
        index={1}
        kind="image"
        onIndexChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByTestId('lightbox-skeleton')).toBeTruthy();
  });
});
