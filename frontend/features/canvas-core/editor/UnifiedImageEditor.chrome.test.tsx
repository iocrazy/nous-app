// IC 图二 chrome: white rounded card, download button, preview wheel-zoom
// with a zoom% readout (IC imageEditModal bottom-left).
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { UnifiedImageEditor } from './UnifiedImageEditor';

vi.mock('../smart/mediaUrl', () => ({ mediaSrc: (u: string) => u }));

describe('UnifiedImageEditor IC chrome', () => {
  it('renders a download button pointing at the image source', () => {
    render(<UnifiedImageEditor open src="/img.png" alt="x" onClose={() => {}} />);
    const dl = screen.getByTestId('editor-download');
    expect(dl).toHaveAttribute('href', '/img.png');
    expect(dl).toHaveAttribute('download');
  });

  it('shows 100% zoom readout and zooms with the wheel in preview', () => {
    render(<UnifiedImageEditor open src="/img.png" alt="x" onClose={() => {}} />);
    expect(screen.getByTestId('editor-zoom')).toHaveTextContent('100%');
    fireEvent.wheel(screen.getByTestId('editor-stage'), { deltaY: -100 });
    expect(screen.getByTestId('editor-zoom')).not.toHaveTextContent('100%');
  });
});
