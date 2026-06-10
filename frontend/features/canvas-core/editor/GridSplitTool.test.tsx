import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { GridSplitTool } from './GridSplitTool';
import { type GridLines } from './gridMath';

function Host({
  initial,
  onSnapshot,
}: {
  initial: GridLines;
  onSnapshot?: (lines: GridLines) => void;
}) {
  const [value, setValue] = useState<GridLines>(initial);
  const handleChange = (next: GridLines) => {
    setValue(next);
    onSnapshot?.(next);
  };
  return (
    <GridSplitTool
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
  // 1000 × 500 fake layout so 0.1 of width = 100px.
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

function dispatchPointer(
  type: 'pointerdown' | 'pointermove' | 'pointerup',
  target: Element | Window,
  init: {
    pointerId?: number;
    movementX?: number;
    movementY?: number;
  } = {},
) {
  const event = new PointerEvent(type, {
    bubbles: true,
    cancelable: true,
    pointerId: init.pointerId ?? 1,
  });
  if (init.movementX !== undefined) {
    Object.defineProperty(event, 'movementX', { value: init.movementX });
  }
  if (init.movementY !== undefined) {
    Object.defineProperty(event, 'movementY', { value: init.movementY });
  }
  target.dispatchEvent(event);
}

describe('GridSplitTool', () => {
  it('renders one separator per split line', () => {
    render(<Host initial={{ xs: [0.3, 0.7], ys: [0.5] }} />);
    expect(screen.getByTestId('grid-line-x-0')).toBeInTheDocument();
    expect(screen.getByTestId('grid-line-x-1')).toBeInTheDocument();
    expect(screen.getByTestId('grid-line-y-0')).toBeInTheDocument();
    expect(screen.queryByTestId('grid-line-y-1')).not.toBeInTheDocument();
  });

  it('positions lines by percentage', () => {
    render(<Host initial={{ xs: [0.3], ys: [0.5] }} />);
    expect(screen.getByTestId('grid-line-x-0').style.left).toBe('30%');
    expect(screen.getByTestId('grid-line-y-0').style.top).toBe('50%');
  });

  it('dragging a vertical line moves it horizontally', () => {
    const snapshots: GridLines[] = [];
    render(
      <Host
        initial={{ xs: [0.5], ys: [] }}
        onSnapshot={(lines) => snapshots.push(lines)}
      />,
    );
    const line = screen.getByTestId('grid-line-x-0');
    fireEvent.pointerDown(line, { pointerId: 1 });
    // +100px on a 1000px-wide rect = +0.1 normalized.
    dispatchPointer('pointermove', window, { movementX: 100 });
    fireEvent.pointerUp(line, { pointerId: 1 });
    const last = snapshots.at(-1);
    expect(last?.xs[0]).toBeCloseTo(0.6);
  });

  it('dragging a horizontal line moves it vertically', () => {
    const snapshots: GridLines[] = [];
    render(
      <Host
        initial={{ xs: [], ys: [0.5] }}
        onSnapshot={(lines) => snapshots.push(lines)}
      />,
    );
    const line = screen.getByTestId('grid-line-y-0');
    fireEvent.pointerDown(line, { pointerId: 1 });
    // -100px on a 500px-tall rect = -0.2 normalized.
    dispatchPointer('pointermove', window, { movementY: -100 });
    fireEvent.pointerUp(line, { pointerId: 1 });
    const last = snapshots.at(-1);
    expect(last?.ys[0]).toBeCloseTo(0.3);
  });

  it('drag clamps against a neighbouring line', () => {
    const snapshots: GridLines[] = [];
    render(
      <Host
        initial={{ xs: [0.5, 0.6], ys: [] }}
        onSnapshot={(lines) => snapshots.push(lines)}
      />,
    );
    const line = screen.getByTestId('grid-line-x-0');
    fireEvent.pointerDown(line, { pointerId: 1 });
    dispatchPointer('pointermove', window, { movementX: 400 });
    fireEvent.pointerUp(line, { pointerId: 1 });
    const last = snapshots.at(-1);
    expect(last?.xs[0]).toBeLessThan(0.6);
    expect(last?.xs[1]).toBe(0.6);
  });

  it('double-click removes a line', () => {
    const snapshots: GridLines[] = [];
    render(
      <Host
        initial={{ xs: [0.5], ys: [0.5] }}
        onSnapshot={(lines) => snapshots.push(lines)}
      />,
    );
    fireEvent.doubleClick(screen.getByTestId('grid-line-x-0'));
    const last = snapshots.at(-1);
    expect(last?.xs).toEqual([]);
    expect(last?.ys).toEqual([0.5]);
  });

  it('pointer moves after pointerup do not change lines', () => {
    const snapshots: GridLines[] = [];
    render(
      <Host
        initial={{ xs: [0.5], ys: [] }}
        onSnapshot={(lines) => snapshots.push(lines)}
      />,
    );
    const line = screen.getByTestId('grid-line-x-0');
    fireEvent.pointerDown(line, { pointerId: 1 });
    fireEvent.pointerUp(line, { pointerId: 1 });
    const countAfterUp = snapshots.length;
    dispatchPointer('pointermove', window, { movementX: 100 });
    expect(snapshots.length).toBe(countAfterUp);
  });
});
