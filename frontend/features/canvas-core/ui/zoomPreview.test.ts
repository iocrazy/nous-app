// IC Z-overview (enterZoomPreview/exitZoomPreview): first Z remembers the
// viewport and fits everything; second Z restores the remembered viewport.
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { isZoomPreview, resetZoomPreview, toggleZoomPreview } from './zoomPreview';

describe('toggleZoomPreview', () => {
  const inst = {
    getViewport: vi.fn(() => ({ x: 10, y: 20, zoom: 1.5 })),
    setViewport: vi.fn(),
    fitView: vi.fn(),
  };

  beforeEach(() => {
    resetZoomPreview();
    vi.clearAllMocks();
  });

  it('first toggle saves the viewport and fits all', () => {
    toggleZoomPreview(inst as never);
    expect(inst.fitView).toHaveBeenCalled();
    expect(isZoomPreview()).toBe(true);
  });

  it('second toggle restores the saved viewport', () => {
    toggleZoomPreview(inst as never);
    toggleZoomPreview(inst as never);
    expect(inst.setViewport).toHaveBeenCalledWith(
      { x: 10, y: 20, zoom: 1.5 },
      { duration: 220 },
    );
    expect(isZoomPreview()).toBe(false);
  });
});
