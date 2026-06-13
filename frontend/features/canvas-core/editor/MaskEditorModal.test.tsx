import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MaskEditorModal } from './MaskEditorModal';
import { type MaskStroke } from './maskMath';

const SRC = 'data:image/png;base64,iVBORw0KGgo=';

const ORIGINAL_GET_BOUNDING = HTMLElement.prototype.getBoundingClientRect;
const ORIGINAL_SET_CAPTURE = HTMLElement.prototype.setPointerCapture;

beforeEach(() => {
  HTMLElement.prototype.getBoundingClientRect = function fakeRect() {
    return {
      x: 0,
      y: 0,
      width: 1000,
      height: 500,
      top: 0,
      left: 0,
      bottom: 500,
      right: 1000,
      toJSON: () => ({}),
    } as DOMRect;
  };
  HTMLElement.prototype.setPointerCapture = () => {};
});

afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = ORIGINAL_GET_BOUNDING;
  HTMLElement.prototype.setPointerCapture = ORIGINAL_SET_CAPTURE;
});

function renderModal(
  overrides: Partial<Parameters<typeof MaskEditorModal>[0]> = {},
) {
  const onCommit = vi.fn();
  const onCancel = vi.fn();
  const utils = render(
    <MaskEditorModal
      open
      src={SRC}
      onCommit={onCommit}
      onCancel={onCancel}
      {...overrides}
    />,
  );
  return { onCommit, onCancel, ...utils };
}

/** Paint one brush stroke across the painter surface. */
function paintStroke() {
  const surface = screen.getByTestId('mask-brush-tool');
  fireEvent.pointerDown(surface, { pointerId: 1, clientX: 100, clientY: 100 });
  fireEvent.pointerUp(surface, { pointerId: 1 });
}

describe('MaskEditorModal — visibility and tools', () => {
  it('renders nothing when closed', () => {
    render(
      <MaskEditorModal
        open={false}
        src={SRC}
        onCommit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('mask-editor-modal')).not.toBeInTheDocument();
  });

  it('starts with brush + medium size active and Cut Out disabled', () => {
    renderModal();
    expect(screen.getByTestId('mask-tool-brush')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByTestId('mask-size-m')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByTestId('mask-editor-commit')).toBeDisabled();
    expect(screen.getByTestId('mask-clear')).toBeDisabled();
  });

  it('tool and size toggles switch the active state', () => {
    renderModal();
    fireEvent.click(screen.getByTestId('mask-tool-eraser'));
    expect(screen.getByTestId('mask-tool-eraser')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    fireEvent.click(screen.getByTestId('mask-size-l'));
    expect(screen.getByTestId('mask-size-l')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });
});

describe('MaskEditorModal — painting and commit', () => {
  it('painting a brush stroke enables Cut Out and renders the stroke', () => {
    renderModal();
    paintStroke();
    expect(screen.getByTestId('mask-stroke-0')).toBeInTheDocument();
    expect(screen.getByTestId('mask-stroke-0').getAttribute('data-tool')).toBe(
      'brush',
    );
    expect(screen.getByTestId('mask-editor-commit')).toBeEnabled();
  });

  it('eraser-only strokes leave Cut Out disabled', () => {
    renderModal();
    fireEvent.click(screen.getByTestId('mask-tool-eraser'));
    paintStroke();
    expect(screen.getByTestId('mask-stroke-0').getAttribute('data-tool')).toBe(
      'eraser',
    );
    expect(screen.getByTestId('mask-editor-commit')).toBeDisabled();
  });

  it('Cut Out passes strokes + fallback size to onCommit', () => {
    const { onCommit } = renderModal();
    paintStroke();
    fireEvent.click(screen.getByTestId('mask-editor-commit'));
    expect(onCommit).toHaveBeenCalledTimes(1);
    const [strokes, size] = onCommit.mock.calls[0] as [
      MaskStroke[],
      { width: number; height: number },
    ];
    expect(strokes).toHaveLength(1);
    expect(strokes[0].tool).toBe('brush');
    // jsdom images never load, so the fallback raster size applies.
    expect(size).toEqual({ width: 1024, height: 1024 });
  });

  it('Clear wipes strokes and disables Cut Out again', () => {
    renderModal();
    paintStroke();
    fireEvent.click(screen.getByTestId('mask-clear'));
    expect(screen.queryByTestId('mask-stroke-0')).not.toBeInTheDocument();
    expect(screen.getByTestId('mask-editor-commit')).toBeDisabled();
  });

  it('re-opening resets strokes', () => {
    const onCommit = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <MaskEditorModal open src={SRC} onCommit={onCommit} onCancel={onCancel} />,
    );
    paintStroke();
    expect(screen.getByTestId('mask-stroke-0')).toBeInTheDocument();
    rerender(
      <MaskEditorModal
        open={false}
        src={SRC}
        onCommit={onCommit}
        onCancel={onCancel}
      />,
    );
    rerender(
      <MaskEditorModal open src={SRC} onCommit={onCommit} onCancel={onCancel} />,
    );
    expect(screen.queryByTestId('mask-stroke-0')).not.toBeInTheDocument();
  });
});

describe('MaskEditorModal — cancel and committing state', () => {
  it('Cancel, backdrop and Escape route through onCancel', () => {
    const { onCommit, onCancel } = renderModal();
    fireEvent.click(screen.getByTestId('mask-editor-cancel'));
    fireEvent.click(screen.getByTestId('mask-editor-modal'));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(3);
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('committing disables all controls and ignores Esc/backdrop', () => {
    const { onCancel, rerender, onCommit } = renderModal();
    paintStroke();
    rerender(
      <MaskEditorModal
        open
        src={SRC}
        onCommit={onCommit}
        onCancel={onCancel}
        committing
      />,
    );
    expect(screen.getByTestId('mask-editor-commit')).toBeDisabled();
    expect(screen.getByTestId('mask-editor-commit').textContent).toContain(
      'Cutting out…',
    );
    expect(screen.getByTestId('mask-editor-cancel')).toBeDisabled();
    expect(screen.getByTestId('mask-tool-brush')).toBeDisabled();
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(screen.getByTestId('mask-editor-modal'));
    expect(onCancel).not.toHaveBeenCalled();
  });
});
