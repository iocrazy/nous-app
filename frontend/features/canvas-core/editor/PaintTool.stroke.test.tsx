// Brush stroke surface (IC canvas rewrite): the paint canvas mounts inside
// the brush tab and survives pointer events (jsdom has no 2d context, so
// pixel output is covered by the prod walkthrough, not here).
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { UnifiedImageEditor } from './UnifiedImageEditor';

vi.mock('../smart/mediaUrl', () => ({
  mediaSrc: (u: string) => u,
  fullResSrc: (u: string) => u,
  fullResPath: (u: string) => u,
}));

describe('brush canvas', () => {
  it('mounts the paint canvas in the brush tab and tolerates pointer input', () => {
    render(
      <UnifiedImageEditor open src="/img.png" onClose={() => {}} onBrushCommit={() => {}} />,
    );
    fireEvent.click(screen.getByTestId('editor-tab-brush'));
    const canvas = screen.getByTestId('paint-canvas');
    fireEvent.pointerDown(canvas, { clientX: 10, clientY: 10, pointerId: 1, button: 0 });
    fireEvent.pointerMove(canvas, { clientX: 40, clientY: 30, pointerId: 1 });
    fireEvent.pointerUp(canvas, { pointerId: 1 });
    expect(canvas).toBeInTheDocument();
  });
});
