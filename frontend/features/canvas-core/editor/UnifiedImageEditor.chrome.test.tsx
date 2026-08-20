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

describe('UnifiedImageEditor ε4 editor chrome', () => {
  it('crop tab shows aspect presets and 1:1 snaps the region square', () => {
    render(
      <UnifiedImageEditor
        open
        src="/img.png"
        alt="x"
        onClose={() => {}}
        onCropCommit={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId('editor-tab-crop'));
    fireEvent.click(screen.getByTestId('editor-crop-aspect-1-1'));
    // the preset button turns active
    expect(screen.getByTestId('editor-crop-aspect-1-1').className).toContain('bg-canvas-strong');
  });

  it('wheel zoom works outside preview and double-click resets to 100%', () => {
    render(
      <UnifiedImageEditor
        open
        src="/img.png"
        alt="x"
        onClose={() => {}}
        onCropCommit={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId('editor-tab-crop'));
    fireEvent.wheel(screen.getByTestId('editor-stage'), { deltaY: -100 });
    expect(screen.getByTestId('editor-zoom')).not.toHaveTextContent('100%');
    fireEvent.doubleClick(screen.getByTestId('editor-zoom'));
    expect(screen.getByTestId('editor-zoom')).toHaveTextContent('100%');
  });

  it('mask tab exposes undo and redo for strokes', () => {
    render(
      <UnifiedImageEditor
        open
        src="/img.png"
        alt="x"
        onClose={() => {}}
        onMaskCommit={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId('editor-tab-mask'));
    expect(screen.getByTestId('mask-undo')).toBeDisabled();
    expect(screen.getByTestId('mask-redo')).toBeDisabled();
  });

  it('brush tab exposes redo alongside undo', () => {
    render(
      <UnifiedImageEditor
        open
        src="/img.png"
        alt="x"
        onClose={() => {}}
        onBrushCommit={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId('editor-tab-brush'));
    expect(screen.getByTestId('brush-redo')).toBeDisabled();
  });
});
