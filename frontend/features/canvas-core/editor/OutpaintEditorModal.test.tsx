import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { OutpaintEditorModal } from './OutpaintEditorModal';
import { type OutpaintPadding } from './outpaintMath';

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
  overrides: Partial<Parameters<typeof OutpaintEditorModal>[0]> = {},
) {
  const onCommit = vi.fn();
  const onCancel = vi.fn();
  const utils = render(
    <OutpaintEditorModal
      open
      src={SRC}
      onCommit={onCommit}
      onCancel={onCancel}
      {...overrides}
    />,
  );
  return { onCommit, onCancel, ...utils };
}

/** Simulate the source image finishing its load at 800×400. */
function loadImageAt(width: number, height: number) {
  const img = screen.getByTestId('outpaint-source-image');
  Object.defineProperty(img, 'naturalWidth', { value: width });
  Object.defineProperty(img, 'naturalHeight', { value: height });
  fireEvent.load(img);
}

/** Drag the right handle outward by `px` (image rect is 1000 wide). */
function dragRightBy(px: number) {
  const handle = screen.getByTestId('outpaint-handle-right');
  fireEvent.pointerDown(handle, { pointerId: 1, clientX: 500, clientY: 250 });
  window.dispatchEvent(
    new PointerEvent('pointermove', {
      bubbles: true,
      pointerId: 1,
      clientX: 500 + px,
      clientY: 250,
    }),
  );
  fireEvent.pointerUp(handle, { pointerId: 1 });
}

describe('OutpaintEditorModal — visibility and gating', () => {
  it('renders nothing when closed', () => {
    render(
      <OutpaintEditorModal
        open={false}
        src={SRC}
        onCommit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(
      screen.queryByTestId('outpaint-editor-modal'),
    ).not.toBeInTheDocument();
  });

  it('Extend and Reset start disabled; aspect presets wait for image load', () => {
    renderModal();
    expect(screen.getByTestId('outpaint-editor-commit')).toBeDisabled();
    expect(screen.getByTestId('outpaint-clear')).toBeDisabled();
    expect(screen.getByTestId('outpaint-aspect-16-9')).toBeDisabled();
    expect(screen.getByTestId('outpaint-resolution').textContent).toMatch(
      /pending/i,
    );
  });

  it('dragging a handle enables Extend', () => {
    renderModal();
    dragRightBy(100);
    expect(screen.getByTestId('outpaint-editor-commit')).toBeEnabled();
  });
});

describe('OutpaintEditorModal — aspect presets and resolution readout', () => {
  it('16:9 preset on a square image pads left/right and updates the readout', () => {
    renderModal();
    loadImageAt(900, 900);
    const aspectButton = screen.getByTestId('outpaint-aspect-16-9');
    expect(aspectButton).toBeEnabled();
    fireEvent.click(aspectButton);
    // 900×900 → 1600×900.
    expect(screen.getByTestId('outpaint-resolution').textContent).toBe(
      '900 × 900 → 1600 × 900',
    );
    expect(screen.getByTestId('outpaint-editor-commit')).toBeEnabled();
  });

  it('Reset returns to zero padding and disables Extend', () => {
    renderModal();
    loadImageAt(900, 900);
    fireEvent.click(screen.getByTestId('outpaint-aspect-9-16'));
    fireEvent.click(screen.getByTestId('outpaint-clear'));
    expect(screen.getByTestId('outpaint-resolution').textContent).toBe(
      '900 × 900 → 900 × 900',
    );
    expect(screen.getByTestId('outpaint-editor-commit')).toBeDisabled();
  });
});

describe('OutpaintEditorModal — prompt and commit', () => {
  it('prompt is prefilled from initialPrompt and editable', () => {
    renderModal({ initialPrompt: 'a windswept meadow' });
    const prompt = screen.getByTestId(
      'outpaint-prompt',
    ) as HTMLTextAreaElement;
    expect(prompt.value).toBe('a windswept meadow');
    fireEvent.change(prompt, { target: { value: 'mountains at dusk' } });
    expect(prompt.value).toBe('mountains at dusk');
  });

  it('Extend passes padding + trimmed prompt to onCommit', () => {
    const { onCommit } = renderModal({ initialPrompt: '  meadow  ' });
    dragRightBy(100);
    fireEvent.click(screen.getByTestId('outpaint-editor-commit'));
    expect(onCommit).toHaveBeenCalledTimes(1);
    const [padding, prompt] = onCommit.mock.calls[0] as [
      OutpaintPadding,
      string,
    ];
    expect(padding.right).toBeCloseTo(0.1);
    expect(prompt).toBe('meadow');
  });

  it('re-opening resets padding and prompt', () => {
    const onCommit = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <OutpaintEditorModal
        open
        src={SRC}
        initialPrompt="base"
        onCommit={onCommit}
        onCancel={onCancel}
      />,
    );
    dragRightBy(100);
    fireEvent.change(screen.getByTestId('outpaint-prompt'), {
      target: { value: 'edited' },
    });
    rerender(
      <OutpaintEditorModal
        open={false}
        src={SRC}
        initialPrompt="base"
        onCommit={onCommit}
        onCancel={onCancel}
      />,
    );
    rerender(
      <OutpaintEditorModal
        open
        src={SRC}
        initialPrompt="base"
        onCommit={onCommit}
        onCancel={onCancel}
      />,
    );
    expect(screen.getByTestId('outpaint-editor-commit')).toBeDisabled();
    expect(
      (screen.getByTestId('outpaint-prompt') as HTMLTextAreaElement).value,
    ).toBe('base');
  });
});

describe('OutpaintEditorModal — cancel and committing state', () => {
  it('Cancel, backdrop and Escape route through onCancel', () => {
    const { onCommit, onCancel } = renderModal();
    fireEvent.click(screen.getByTestId('outpaint-editor-cancel'));
    fireEvent.click(screen.getByTestId('outpaint-editor-modal'));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(3);
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('committing freezes all controls and ignores Esc/backdrop', () => {
    const onCommit = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <OutpaintEditorModal
        open
        src={SRC}
        onCommit={onCommit}
        onCancel={onCancel}
      />,
    );
    dragRightBy(100);
    rerender(
      <OutpaintEditorModal
        open
        src={SRC}
        onCommit={onCommit}
        onCancel={onCancel}
        committing
      />,
    );
    expect(screen.getByTestId('outpaint-editor-commit')).toBeDisabled();
    expect(
      screen.getByTestId('outpaint-editor-commit').textContent,
    ).toContain('Extending…');
    expect(screen.getByTestId('outpaint-prompt')).toBeDisabled();
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(screen.getByTestId('outpaint-editor-modal'));
    expect(onCancel).not.toHaveBeenCalled();
  });
});
