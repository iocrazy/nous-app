import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { GridSplitEditorModal } from './GridSplitEditorModal';
import { MAX_LINES_PER_AXIS, type GridLines } from './gridMath';

const SRC = 'data:image/png;base64,iVBORw0KGgo=';

function renderModal(overrides: Partial<Parameters<typeof GridSplitEditorModal>[0]> = {}) {
  const onCommit = vi.fn();
  const onCancel = vi.fn();
  const utils = render(
    <GridSplitEditorModal
      open
      src={SRC}
      onCommit={onCommit}
      onCancel={onCancel}
      {...overrides}
    />,
  );
  return { onCommit, onCancel, ...utils };
}

describe('GridSplitEditorModal — visibility', () => {
  it('renders nothing when closed', () => {
    render(
      <GridSplitEditorModal
        open={false}
        src={SRC}
        onCommit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('grid-editor-modal')).not.toBeInTheDocument();
  });

  it('renders the dialog, toolbar and editor when open', () => {
    renderModal();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByTestId('grid-toolbar')).toBeInTheDocument();
    expect(screen.getByTestId('grid-split-tool')).toBeInTheDocument();
  });
});

describe('GridSplitEditorModal — presets and toolbar', () => {
  it('starts with no lines: 1 tile and Split disabled', () => {
    renderModal();
    expect(screen.getByTestId('grid-tile-count').textContent).toContain(
      '1 tiles (1 × 1)',
    );
    expect(screen.getByTestId('grid-editor-commit')).toBeDisabled();
    expect(screen.getByTestId('grid-clear')).toBeDisabled();
  });

  it('2×2 preset yields 4 tiles and enables Split', () => {
    renderModal();
    fireEvent.click(screen.getByTestId('grid-preset-2x2'));
    expect(screen.getByTestId('grid-tile-count').textContent).toContain(
      '4 tiles (2 × 2)',
    );
    expect(screen.getByTestId('grid-editor-commit')).toBeEnabled();
  });

  it('3×3 preset replaces previous lines instead of stacking', () => {
    renderModal();
    fireEvent.click(screen.getByTestId('grid-preset-4x4'));
    fireEvent.click(screen.getByTestId('grid-preset-3x3'));
    expect(screen.getByTestId('grid-tile-count').textContent).toContain(
      '9 tiles (3 × 3)',
    );
  });

  it('+ Vertical / + Horizontal add one line each', () => {
    renderModal();
    fireEvent.click(screen.getByTestId('grid-add-vertical'));
    expect(screen.getByTestId('grid-tile-count').textContent).toContain(
      '2 tiles (1 × 2)',
    );
    fireEvent.click(screen.getByTestId('grid-add-horizontal'));
    expect(screen.getByTestId('grid-tile-count').textContent).toContain(
      '4 tiles (2 × 2)',
    );
  });

  it('+ Vertical stops adding at the per-axis cap', () => {
    renderModal();
    for (let i = 0; i < MAX_LINES_PER_AXIS + 3; i += 1) {
      fireEvent.click(screen.getByTestId('grid-add-vertical'));
    }
    expect(screen.getByTestId('grid-tile-count').textContent).toContain(
      `${MAX_LINES_PER_AXIS + 1} tiles (1 × ${MAX_LINES_PER_AXIS + 1})`,
    );
  });

  it('Clear removes all lines and disables Split again', () => {
    renderModal();
    fireEvent.click(screen.getByTestId('grid-preset-3x3'));
    fireEvent.click(screen.getByTestId('grid-clear'));
    expect(screen.getByTestId('grid-tile-count').textContent).toContain(
      '1 tiles (1 × 1)',
    );
    expect(screen.getByTestId('grid-editor-commit')).toBeDisabled();
  });
});

describe('GridSplitEditorModal — commit and cancel', () => {
  it('Split passes the current lines to onCommit', () => {
    const { onCommit } = renderModal();
    fireEvent.click(screen.getByTestId('grid-preset-2x2'));
    fireEvent.click(screen.getByTestId('grid-editor-commit'));
    expect(onCommit).toHaveBeenCalledTimes(1);
    const lines = onCommit.mock.calls[0][0] as GridLines;
    expect(lines.xs).toEqual([0.5]);
    expect(lines.ys).toEqual([0.5]);
  });

  it('Cancel and backdrop route through onCancel without committing', () => {
    const { onCommit, onCancel } = renderModal();
    fireEvent.click(screen.getByTestId('grid-editor-cancel'));
    fireEvent.click(screen.getByTestId('grid-editor-modal'));
    expect(onCancel).toHaveBeenCalledTimes(2);
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('Escape routes through onCancel', () => {
    const { onCancel } = renderModal();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('re-opening resets the in-progress lines', () => {
    const onCommit = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <GridSplitEditorModal open src={SRC} onCommit={onCommit} onCancel={onCancel} />,
    );
    fireEvent.click(screen.getByTestId('grid-preset-3x3'));
    rerender(
      <GridSplitEditorModal
        open={false}
        src={SRC}
        onCommit={onCommit}
        onCancel={onCancel}
      />,
    );
    rerender(
      <GridSplitEditorModal open src={SRC} onCommit={onCommit} onCancel={onCancel} />,
    );
    expect(screen.getByTestId('grid-tile-count').textContent).toContain(
      '1 tiles (1 × 1)',
    );
  });
});

describe('GridSplitEditorModal — committing state', () => {
  it('disables all controls and shows the busy label', () => {
    renderModal({ committing: true, initialLines: { xs: [0.5], ys: [] } });
    expect(screen.getByTestId('grid-editor-commit')).toBeDisabled();
    expect(screen.getByTestId('grid-editor-commit').textContent).toContain(
      'Splitting…',
    );
    expect(screen.getByTestId('grid-editor-cancel')).toBeDisabled();
    expect(screen.getByTestId('grid-editor-close')).toBeDisabled();
    expect(screen.getByTestId('grid-preset-2x2')).toBeDisabled();
    expect(screen.getByTestId('grid-add-vertical')).toBeDisabled();
  });

  it('Escape and backdrop are ignored while committing', () => {
    const { onCancel } = renderModal({
      committing: true,
      initialLines: { xs: [0.5], ys: [] },
    });
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(screen.getByTestId('grid-editor-modal'));
    expect(onCancel).not.toHaveBeenCalled();
  });
});
