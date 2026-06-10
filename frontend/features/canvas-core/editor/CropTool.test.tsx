import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CropTool } from './CropTool';
import { FULL_REGION, type CropRegion } from './types';

function Host({ initial }: { initial?: CropRegion }) {
  const [value, setValue] = useState<CropRegion>(initial ?? FULL_REGION);
  return (
    <CropTool
      src="data:image/png;base64,iVBORw0KGgo="
      alt="test"
      value={value}
      onChange={setValue}
    />
  );
}

const ORIGINAL_GET_BOUNDING = HTMLElement.prototype.getBoundingClientRect;

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
});

afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = ORIGINAL_GET_BOUNDING;
});

function dispatchPointer(
  type: 'pointerdown' | 'pointermove' | 'pointerup',
  target: Element | Window,
  init: Partial<PointerEvent> & { clientX?: number; clientY?: number; movementX?: number; movementY?: number; pointerId?: number },
) {
  const event = new PointerEvent(type, {
    bubbles: true,
    cancelable: true,
    pointerId: init.pointerId ?? 1,
    clientX: init.clientX ?? 0,
    clientY: init.clientY ?? 0,
    // movementX / movementY are read by the component; jsdom's
    // PointerEvent doesn't populate them from the previous event, so
    // we set them directly via defineProperty.
  });
  if (init.movementX !== undefined) {
    Object.defineProperty(event, 'movementX', { value: init.movementX });
  }
  if (init.movementY !== undefined) {
    Object.defineProperty(event, 'movementY', { value: init.movementY });
  }
  target.dispatchEvent(event);
}

describe('CropTool — render', () => {
  it('renders the image + the 8 handles + the move grip', () => {
    render(<Host />);
    expect(screen.getByAltText('test')).toBeInTheDocument();
    for (const h of ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'] as const) {
      expect(screen.getByTestId(`crop-handle-${h}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId('crop-move')).toBeInTheDocument();
  });

  it('exposes the crop rectangle as role=region with an accessible name', () => {
    render(<Host />);
    expect(
      screen.getByRole('region', { name: /crop selection/i }),
    ).toBeInTheDocument();
  });
});

describe('CropTool — drag SE handle grows the rect', () => {
  it('dragging SE by +100px right + +50px down adds 0.1 / 0.1', () => {
    render(<Host initial={{ x: 0.25, y: 0.25, width: 0.5, height: 0.5 }} />);
    const se = screen.getByTestId('crop-handle-se');
    se.setPointerCapture = vi.fn();
    fireEvent.pointerDown(se, { pointerId: 1, clientX: 0, clientY: 0 });
    dispatchPointer('pointermove', window, {
      pointerId: 1,
      movementX: 100,
      movementY: 50,
    });
    fireEvent.pointerUp(se, { pointerId: 1 });

    // 100/1000 = 0.1 wider, 50/500 = 0.1 taller.
    const region = screen.getByRole('region', { name: /crop selection/i });
    const inlineStyle = (region as HTMLElement).style;
    // x/y stay 25%, width/height grow to ~60%.
    expect(inlineStyle.left).toBe('25%');
    expect(inlineStyle.top).toBe('25%');
    expect(inlineStyle.width).toBe('60%');
    expect(inlineStyle.height).toBe('60%');
  });
});

describe('CropTool — drag MOVE grip translates the rect', () => {
  it('dragging move by +100px right shifts x by +0.1', () => {
    render(<Host initial={{ x: 0.1, y: 0.1, width: 0.3, height: 0.3 }} />);
    const grip = screen.getByTestId('crop-move');
    grip.setPointerCapture = vi.fn();
    fireEvent.pointerDown(grip, { pointerId: 1, clientX: 0, clientY: 0 });
    dispatchPointer('pointermove', window, {
      pointerId: 1,
      movementX: 100,
      movementY: 0,
    });
    fireEvent.pointerUp(grip, { pointerId: 1 });

    const region = screen.getByRole('region', { name: /crop selection/i });
    const inlineStyle = (region as HTMLElement).style;
    expect(inlineStyle.left).toBe('20%');
    expect(inlineStyle.top).toBe('10%');
    expect(inlineStyle.width).toBe('30%');
    expect(inlineStyle.height).toBe('30%');
  });
});

describe('CropTool — handle drag releases on pointerup', () => {
  it('after pointerup, further pointermoves do not change the rect', () => {
    render(<Host initial={{ x: 0.2, y: 0.2, width: 0.4, height: 0.4 }} />);
    const se = screen.getByTestId('crop-handle-se');
    se.setPointerCapture = vi.fn();
    fireEvent.pointerDown(se, { pointerId: 1, clientX: 0, clientY: 0 });
    fireEvent.pointerUp(se, { pointerId: 1 });

    // Subsequent move (after pointerup) should be ignored.
    dispatchPointer('pointermove', window, {
      pointerId: 1,
      movementX: 500,
      movementY: 500,
    });

    const region = screen.getByRole('region', { name: /crop selection/i });
    const inlineStyle = (region as HTMLElement).style;
    expect(inlineStyle.width).toBe('40%');
    expect(inlineStyle.height).toBe('40%');
  });
});
