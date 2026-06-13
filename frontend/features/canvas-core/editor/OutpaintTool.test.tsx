import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { OutpaintTool } from './OutpaintTool';
import { ZERO_PADDING, type OutpaintPadding } from './outpaintMath';

function Host({
  initial,
  onSnapshot,
}: {
  initial?: OutpaintPadding;
  onSnapshot?: (padding: OutpaintPadding) => void;
}) {
  const [value, setValue] = useState<OutpaintPadding>(initial ?? ZERO_PADDING);
  const handleChange = (next: OutpaintPadding) => {
    setValue(next);
    onSnapshot?.(next);
  };
  return (
    <OutpaintTool
      src="data:image/png;base64,iVBORw0KGgo="
      alt="test"
      value={value}
      onChange={handleChange}
    />
  );
}

const ORIGINAL_GET_BOUNDING = HTMLElement.prototype.getBoundingClientRect;
const ORIGINAL_SET_CAPTURE = HTMLElement.prototype.setPointerCapture;

beforeEach(() => {
  // Image rect 1000 × 500 → 100px of horizontal drag = 0.1 padding.
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

function dragPointer(
  handle: Element,
  from: { x: number; y: number },
  to: { x: number; y: number },
) {
  fireEvent.pointerDown(handle, {
    pointerId: 1,
    clientX: from.x,
    clientY: from.y,
  });
  const move = new PointerEvent('pointermove', {
    bubbles: true,
    pointerId: 1,
    clientX: to.x,
    clientY: to.y,
  });
  window.dispatchEvent(move);
  fireEvent.pointerUp(handle, { pointerId: 1 });
}

describe('OutpaintTool', () => {
  it('renders the four edge handles', () => {
    render(<Host />);
    for (const side of ['left', 'right', 'top', 'bottom'] as const) {
      expect(screen.getByTestId(`outpaint-handle-${side}`)).toBeInTheDocument();
    }
  });

  it('dragging the right handle outward grows right padding', () => {
    const snapshots: OutpaintPadding[] = [];
    render(<Host onSnapshot={(p) => snapshots.push(p)} />);
    dragPointer(
      screen.getByTestId('outpaint-handle-right'),
      { x: 500, y: 250 },
      { x: 600, y: 250 },
    );
    expect(snapshots.at(-1)?.right).toBeCloseTo(0.1);
    expect(snapshots.at(-1)?.left).toBe(0);
  });

  it('dragging the left handle outward (negative x) grows left padding', () => {
    const snapshots: OutpaintPadding[] = [];
    render(<Host onSnapshot={(p) => snapshots.push(p)} />);
    dragPointer(
      screen.getByTestId('outpaint-handle-left'),
      { x: 500, y: 250 },
      { x: 300, y: 250 },
    );
    expect(snapshots.at(-1)?.left).toBeCloseTo(0.2);
  });

  it('dragging the bottom handle down grows bottom padding (height basis)', () => {
    const snapshots: OutpaintPadding[] = [];
    render(<Host onSnapshot={(p) => snapshots.push(p)} />);
    dragPointer(
      screen.getByTestId('outpaint-handle-bottom'),
      { x: 500, y: 250 },
      { x: 500, y: 350 },
    );
    // 100px on a 500px-tall image = 0.2.
    expect(snapshots.at(-1)?.bottom).toBeCloseTo(0.2);
  });

  it('inward drag clamps at zero', () => {
    const snapshots: OutpaintPadding[] = [];
    render(
      <Host
        initial={{ ...ZERO_PADDING, right: 0.1 }}
        onSnapshot={(p) => snapshots.push(p)}
      />,
    );
    dragPointer(
      screen.getByTestId('outpaint-handle-right'),
      { x: 500, y: 250 },
      { x: 0, y: 250 },
    );
    expect(snapshots.at(-1)?.right).toBe(0);
  });

  it('image inset style reflects the padding proportions', () => {
    render(
      <Host initial={{ left: 0.5, top: 0, right: 0.5, bottom: 0 }} />,
    );
    const img = screen.getByTestId('outpaint-source-image');
    // total = 2 → image takes 50% width, offset 25%.
    expect(img.style.width).toBe('50%');
    expect(img.style.left).toBe('25%');
    expect(img.style.height).toBe('100%');
  });
});
