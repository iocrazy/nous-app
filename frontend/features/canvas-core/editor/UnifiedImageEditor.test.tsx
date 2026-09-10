/**
 * UnifiedImageEditor — one modal, seven inline tabs (IC B). Absent commit
 * channels hide their tabs; Apply routes to the active mode's channel.
 */

import { fireEvent, render, screen, cleanup, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { UnifiedImageEditor } from './UnifiedImageEditor';

afterEach(cleanup);

const SRC = '/api/v1/generated-media/1/cover';

describe('UnifiedImageEditor — resolution tier', () => {
  // The editor is where pixels are inspected and edited: crop regions, mask
  // strokes and outpaint padding are all expressed against the image's
  // NATURAL size, so working off the 1024px preview would ship the backend
  // coordinates for the wrong picture. This suite does not stub mediaUrl.
  it('the preview image and the download link both ask for the original', () => {
    render(
      <UnifiedImageEditor open src={SRC} onClose={() => {}} onCropCommit={() => {}} />,
    );
    const download = screen.getByTestId('editor-download') as HTMLAnchorElement;
    expect(download.getAttribute('href')).toMatch(/[?&]full=1$/);
    // The modal renders into a portal, so query the document, not `container`.
    const img = document.querySelector('img') as HTMLImageElement;
    expect(img).toBeTruthy();
    expect(img.getAttribute('src')).toMatch(/[?&]full=1$/);
  });
});

describe('UnifiedImageEditor', () => {
  it('shows only tabs whose commit channel exists', () => {
    render(
      <UnifiedImageEditor
        open
        src={SRC}
        onClose={() => {}}
        onCropCommit={() => {}}
        onBrushCommit={() => {}}
      />,
    );
    expect(screen.getByTestId('editor-tab-preview')).toBeInTheDocument();
    expect(screen.getByTestId('editor-tab-crop')).toBeInTheDocument();
    expect(screen.getByTestId('editor-tab-brush')).toBeInTheDocument();
    expect(screen.queryByTestId('editor-tab-mask')).toBeNull();
    expect(screen.queryByTestId('editor-tab-split')).toBeNull();
  });

  it('opens on the requested tab and Apply routes to that channel', () => {
    const onCropCommit = vi.fn();
    render(
      <UnifiedImageEditor
        open
        src={SRC}
        initialMode="crop"
        onClose={() => {}}
        onCropCommit={onCropCommit}
      />,
    );
    fireEvent.click(screen.getByTestId('editor-apply'));
    expect(onCropCommit).toHaveBeenCalled();
  });

  it('switching tabs swaps the stage tool', () => {
    render(
      <UnifiedImageEditor
        open
        src={SRC}
        onClose={() => {}}
        onCropCommit={() => {}}
        onBrushCommit={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId('editor-tab-brush'));
    expect(screen.getByTestId('paint-canvas')).toBeInTheDocument();
    expect(screen.getByTestId('paint-tool-free')).toBeInTheDocument();
  });

  it('preview has no Apply button', () => {
    render(<UnifiedImageEditor open src={SRC} onClose={() => {}} />);
    expect(screen.queryByTestId('editor-apply')).toBeNull();
  });

  it('resize Apply reports the chosen scale', () => {
    const onResizeCommit = vi.fn();
    render(
      <UnifiedImageEditor
        open
        src={SRC}
        initialMode="resize"
        onClose={() => {}}
        onResizeCommit={onResizeCommit}
      />,
    );
    fireEvent.change(screen.getByLabelText('Resize scale'), {
      target: { value: '0.25' },
    });
    fireEvent.click(screen.getByTestId('editor-apply'));
    expect(onResizeCommit).toHaveBeenCalledWith(0.25);
  });
});

describe('UnifiedImageEditor — commit error', () => {
  // The editor portals to body as a full-screen overlay, so a caller's error
  // rendered OUTSIDE it sits beneath the scrim (or inside a transformed React
  // Flow node) and is unreadable. The reason must render inside the dialog.
  it('renders commitError inside the editor dialog', () => {
    render(
      <UnifiedImageEditor
        open
        src={SRC}
        initialMode="crop"
        onClose={() => {}}
        onCropCommit={() => {}}
        commitError="source image not found"
      />,
    );
    const dialog = screen.getByTestId('unified-image-editor');
    expect(within(dialog).getByTestId('editor-commit-error').textContent).toBe(
      'source image not found',
    );
  });

  it('renders no commit error when none is given', () => {
    render(
      <UnifiedImageEditor open src={SRC} onClose={() => {}} onCropCommit={() => {}} />,
    );
    expect(screen.queryByTestId('editor-commit-error')).toBeNull();
  });
});
