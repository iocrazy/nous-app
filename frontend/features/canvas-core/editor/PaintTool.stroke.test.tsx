// Brush stroke must register a shape (2026-08-21 "画笔画不出来").
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { UnifiedImageEditor } from './UnifiedImageEditor';

vi.mock('../smart/mediaUrl', () => ({ mediaSrc: (u: string) => u }));

describe('brush stroke registers', () => {
  it('pointerdown on paint-tool adds a free polyline', () => {
    render(
      <UnifiedImageEditor open src="/img.png" onClose={() => {}} onBrushCommit={() => {}} />,
    );
    fireEvent.click(screen.getByTestId('editor-tab-brush'));
    const tool = screen.getByTestId('paint-tool');
    fireEvent.pointerDown(tool, { clientX: 10, clientY: 10, pointerId: 1, button: 0 });
    fireEvent.pointerMove(tool, { clientX: 40, clientY: 30, pointerId: 1 });
    fireEvent.pointerUp(tool, { pointerId: 1 });
    expect(tool.querySelectorAll('polyline').length).toBe(1);
  });
});
