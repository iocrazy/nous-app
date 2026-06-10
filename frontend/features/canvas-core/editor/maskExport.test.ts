import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MaskExportError, strokesToMaskPngBase64 } from './maskExport';
import { beginStroke, extendStroke, type MaskStroke } from './maskMath';

interface RecordedOp {
  op: string;
  args: unknown[];
}

class FakeContext {
  ops: RecordedOp[] = [];
  fillStyle = '';
  strokeStyle = '';
  lineWidth = 0;
  lineCap = '';
  lineJoin = '';

  fillRect(...args: unknown[]) {
    this.ops.push({ op: `fillRect:${this.fillStyle}`, args });
  }

  stroke(...args: unknown[]) {
    this.ops.push({
      op: `stroke:${this.strokeStyle}:${this.lineWidth}`,
      args,
    });
  }
}

let fakeCtx: FakeContext;
const ORIGINAL_GET_CONTEXT = HTMLCanvasElement.prototype.getContext;
const ORIGINAL_TO_DATA_URL = HTMLCanvasElement.prototype.toDataURL;

class FakePath2D {
  constructor(public d?: string) {}
}

beforeEach(() => {
  fakeCtx = new FakeContext();
  HTMLCanvasElement.prototype.getContext = function getContext() {
    return fakeCtx as unknown as CanvasRenderingContext2D;
  } as unknown as typeof HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.toDataURL = () =>
    'data:image/png;base64,FAKEB64';
  vi.stubGlobal('Path2D', FakePath2D);
});

afterEach(() => {
  HTMLCanvasElement.prototype.getContext = ORIGINAL_GET_CONTEXT;
  HTMLCanvasElement.prototype.toDataURL = ORIGINAL_TO_DATA_URL;
  vi.unstubAllGlobals();
});

function paintedStrokes(): MaskStroke[] {
  let strokes = beginStroke([], 'brush', 0.1, { x: 0.1, y: 0.1 });
  strokes = extendStroke(strokes, { x: 0.5, y: 0.5 });
  strokes = beginStroke(strokes, 'eraser', 0.05, { x: 0.3, y: 0.3 });
  return strokes;
}

describe('strokesToMaskPngBase64', () => {
  it('returns the raw base64 payload (no data-URL prefix)', () => {
    const b64 = strokesToMaskPngBase64(paintedStrokes(), 200, 100);
    expect(b64).toBe('FAKEB64');
  });

  it('fills black first, strokes brush white then eraser black', () => {
    strokesToMaskPngBase64(paintedStrokes(), 200, 100);
    const ops = fakeCtx.ops.map((o) => o.op);
    expect(ops[0]).toBe('fillRect:#000000');
    // Brush: white, lineWidth = 0.1 * 200 = 20.
    expect(ops[1]).toBe('stroke:#ffffff:20');
    // Eraser: black, lineWidth = 0.05 * 200 = 10.
    expect(ops[2]).toBe('stroke:#000000:10');
    expect(fakeCtx.lineCap).toBe('round');
    expect(fakeCtx.lineJoin).toBe('round');
  });

  it('skips empty strokes', () => {
    const strokes: MaskStroke[] = [{ tool: 'brush', size: 0.1, points: [] }];
    strokesToMaskPngBase64(strokes, 100, 100);
    expect(fakeCtx.ops.map((o) => o.op)).toEqual(['fillRect:#000000']);
  });

  it('throws on degenerate dimensions', () => {
    expect(() => strokesToMaskPngBase64([], 0, 100)).toThrow(MaskExportError);
    expect(() => strokesToMaskPngBase64([], 100, NaN)).toThrow(
      MaskExportError,
    );
  });

  it('throws when no 2D context is available', () => {
    HTMLCanvasElement.prototype.getContext = function getContext() {
      return null;
    } as unknown as typeof HTMLCanvasElement.prototype.getContext;
    expect(() => strokesToMaskPngBase64([], 100, 100)).toThrow(
      MaskExportError,
    );
  });
});
