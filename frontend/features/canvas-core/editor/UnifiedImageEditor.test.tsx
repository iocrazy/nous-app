/**
 * UnifiedImageEditor — one modal, seven inline tabs (IC B). Absent commit
 * channels hide their tabs; Apply routes to the active mode's channel.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { UnifiedImageEditor } from './UnifiedImageEditor';

afterEach(cleanup);

const SRC = '/api/v1/generated-media/1/cover';

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
    expect(screen.getByTestId('paint-tool')).toBeInTheDocument();
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
