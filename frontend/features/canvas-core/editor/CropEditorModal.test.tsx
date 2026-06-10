import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CropEditorModal } from './CropEditorModal';
import { FULL_REGION, type CropRegion } from './types';

const ORIGINAL_GET_BOUNDING = HTMLElement.prototype.getBoundingClientRect;

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
});

afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = ORIGINAL_GET_BOUNDING;
});

interface HostProps {
  initialOpen?: boolean;
  initialRegion?: CropRegion;
  onCommit?: (region: CropRegion) => void;
  onCancel?: () => void;
}

function Host({ initialOpen = true, initialRegion, onCommit, onCancel }: HostProps) {
  const [open, setOpen] = useState(initialOpen);
  return (
    <CropEditorModal
      open={open}
      src="data:image/png;base64,iVBORw0KGgo="
      alt="test"
      initialRegion={initialRegion}
      onCommit={(region) => {
        setOpen(false);
        onCommit?.(region);
      }}
      onCancel={() => {
        setOpen(false);
        onCancel?.();
      }}
    />
  );
}

describe('CropEditorModal — visibility', () => {
  it('renders the dialog when open=true', () => {
    render(<Host />);
    expect(screen.getByRole('dialog', { name: /crop image/i })).toBeInTheDocument();
    expect(screen.getByTestId('crop-editor-commit')).toBeInTheDocument();
    expect(screen.getByTestId('crop-editor-cancel')).toBeInTheDocument();
  });

  it('renders nothing when open=false', () => {
    render(<Host initialOpen={false} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('CropEditorModal — commit ships the current region', () => {
  it('clicking Commit returns the initialRegion when no edits happen', () => {
    const onCommit = vi.fn();
    const seed: CropRegion = { x: 0.2, y: 0.2, width: 0.4, height: 0.4 };
    render(<Host initialRegion={seed} onCommit={onCommit} />);
    fireEvent.click(screen.getByTestId('crop-editor-commit'));
    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(onCommit).toHaveBeenCalledWith(seed);
  });
});

describe('CropEditorModal — cancel paths', () => {
  it('Cancel button fires onCancel', () => {
    const onCancel = vi.fn();
    render(<Host onCancel={onCancel} />);
    fireEvent.click(screen.getByTestId('crop-editor-cancel'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('close icon fires onCancel', () => {
    const onCancel = vi.fn();
    render(<Host onCancel={onCancel} />);
    fireEvent.click(screen.getByTestId('crop-editor-close'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('Escape key fires onCancel', () => {
    const onCancel = vi.fn();
    render(<Host onCancel={onCancel} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('backdrop click fires onCancel', () => {
    const onCancel = vi.fn();
    render(<Host onCancel={onCancel} />);
    // Clicking the dialog itself bubbles up but stopPropagation should
    // block it; we click the backdrop wrapper directly.
    fireEvent.click(screen.getByTestId('crop-editor-modal'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('clicking inside the dialog body does NOT fire onCancel', () => {
    const onCancel = vi.fn();
    render(<Host onCancel={onCancel} />);
    fireEvent.click(screen.getByRole('region', { name: /crop selection/i }));
    expect(onCancel).not.toHaveBeenCalled();
  });
});

describe('CropEditorModal — reset on re-open', () => {
  it('a fresh initialRegion replaces a stale in-progress region', () => {
    const onCommit = vi.fn();
    // 1st mount with region A, commit immediately closes the modal.
    const { rerender } = render(
      <CropEditorModal
        open={true}
        src="data:image/png;base64,iVBORw0KGgo="
        initialRegion={{ x: 0.1, y: 0.1, width: 0.2, height: 0.2 }}
        onCommit={onCommit}
        onCancel={vi.fn()}
      />,
    );
    // Close it.
    rerender(
      <CropEditorModal
        open={false}
        src="data:image/png;base64,iVBORw0KGgo="
        initialRegion={{ x: 0.1, y: 0.1, width: 0.2, height: 0.2 }}
        onCommit={onCommit}
        onCancel={vi.fn()}
      />,
    );
    // Re-open with a different initialRegion B.
    rerender(
      <CropEditorModal
        open={true}
        src="data:image/png;base64,iVBORw0KGgo="
        initialRegion={{ x: 0.5, y: 0.5, width: 0.3, height: 0.3 }}
        onCommit={onCommit}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('crop-editor-commit'));
    expect(onCommit).toHaveBeenCalledWith({
      x: 0.5,
      y: 0.5,
      width: 0.3,
      height: 0.3,
    });
  });

  it('defaults to FULL_REGION when no initialRegion is supplied', () => {
    const onCommit = vi.fn();
    render(
      <CropEditorModal
        open={true}
        src="data:image/png;base64,iVBORw0KGgo="
        onCommit={onCommit}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('crop-editor-commit'));
    expect(onCommit).toHaveBeenCalledWith(FULL_REGION);
  });
});

describe('CropEditorModal — aspect ratio toolbar', () => {
  it('renders all 6 presets including Free', () => {
    render(<Host />);
    expect(screen.getByTestId('crop-aspect-free')).toBeInTheDocument();
    expect(screen.getByTestId('crop-aspect-1-1')).toBeInTheDocument();
    expect(screen.getByTestId('crop-aspect-4-3')).toBeInTheDocument();
    expect(screen.getByTestId('crop-aspect-16-9')).toBeInTheDocument();
    expect(screen.getByTestId('crop-aspect-3-4')).toBeInTheDocument();
    expect(screen.getByTestId('crop-aspect-9-16')).toBeInTheDocument();
  });

  it('clicking 1:1 from a wide rectangle snaps to a square and commits it', () => {
    const onCommit = vi.fn();
    render(
      <Host
        initialRegion={{ x: 0.1, y: 0.4, width: 0.6, height: 0.2 }}
        onCommit={onCommit}
      />,
    );
    fireEvent.click(screen.getByTestId('crop-aspect-1-1'));
    fireEvent.click(screen.getByTestId('crop-editor-commit'));
    expect(onCommit).toHaveBeenCalledTimes(1);
    const committed = onCommit.mock.calls[0][0];
    expect(committed.width).toBeCloseTo(committed.height, 9);
  });

  it('Free does NOT touch the region', () => {
    const onCommit = vi.fn();
    const seed = { x: 0.13, y: 0.27, width: 0.45, height: 0.61 };
    render(<Host initialRegion={seed} onCommit={onCommit} />);
    fireEvent.click(screen.getByTestId('crop-aspect-free'));
    fireEvent.click(screen.getByTestId('crop-editor-commit'));
    expect(onCommit).toHaveBeenCalledWith(seed);
  });

  it('selected preset is announced via aria-pressed', () => {
    render(<Host />);
    fireEvent.click(screen.getByTestId('crop-aspect-16-9'));
    expect(screen.getByTestId('crop-aspect-16-9')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByTestId('crop-aspect-1-1')).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('re-opening the modal clears the previous aspect selection', () => {
    const { rerender } = render(
      <CropEditorModal
        open={true}
        src="data:image/png;base64,iVBORw0KGgo="
        onCommit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('crop-aspect-1-1'));
    expect(screen.getByTestId('crop-aspect-1-1')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    rerender(
      <CropEditorModal
        open={false}
        src="data:image/png;base64,iVBORw0KGgo="
        onCommit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    rerender(
      <CropEditorModal
        open={true}
        src="data:image/png;base64,iVBORw0KGgo="
        onCommit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByTestId('crop-aspect-1-1')).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    expect(screen.getByTestId('crop-aspect-free')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });
});
