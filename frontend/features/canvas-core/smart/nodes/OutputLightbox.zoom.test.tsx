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
    renderBox({ compareUrl: '/gm/0/cover' });
    fireEvent.click(screen.getByRole('button', { name: 'Compare' }));
    const stage = screen.getByTestId('lightbox-stage');
    fireEvent.wheel(stage, { deltaY: -120, clientX: 40, clientY: 40 });
    expect(scaleOf()).toBe(1);
  });
});
