// features/canvas-core/editor/PaintCanvas.tsx
//
// IC-parity 画笔 rewritten to the IC implementation spec (smart-canvas.js
// beginEditDraw/strokeFreeDrawPoint/drawBrushShape/drawNumberLabel — read
// for behaviour, re-implemented here; no code copied):
//   • a 2D <canvas> overlay sized to the image's NATURAL pixels — strokes
//     land as pixels immediately, no SVG round-trip
//   • free strokes: line segments + arc-fill interpolation when the pointer
//     jumps farther than the brush radius (steps of radius*0.35) so fast
//     drags never leave gaps
//   • rect/ellipse: snapshot rubber-band — restore the pre-drag snapshot on
//     every move and redraw the shape from the anchor
//   • label: circled numbers ①-⑳ (U+2460..) with a white outline, font
//     max(18, size*2.2), one click per label
//   • text: click prompts for a string, drawn at the click point
//   • undo/redo/clear: ImageData snapshot stack, cap 40 (EDIT_DRAW_HISTORY_MAX)
//   • coalesced pointer events feed the free stroke for smoothness
// Commit composites base image + overlay into one PNG blob (IC
// applyImageBrush semantics).

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';

import { mediaSrc } from '../smart/mediaUrl';
import { apiFetch } from '../../../services/apiClient';

/** Fetch the base image as a decodable bitmap, or null when it cannot be had.
 *  Relative `/api/...` sources go through `apiFetch` (auth + failover); other
 *  URLs use a plain CORS fetch. Retries once. */
async function fetchBaseBitmap(src: string): Promise<HTMLImageElement | null> {
  const attempt = async (): Promise<Blob | null> => {
    try {
      const res = src.startsWith('/api/')
        ? await apiFetch(src, { method: 'GET' })
        : await fetch(mediaSrc(src), { mode: 'cors' });
      if (!res.ok) return null;
      return await res.blob();
    } catch (err) {
      console.error('[paint] base fetch failed', err);
      return null;
    }
  };
  const blob = (await attempt()) ?? (await attempt());
  if (!blob) return null;
  const url = URL.createObjectURL(blob);
  try {
    return await new Promise<HTMLImageElement | null>((resolve) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = url;
    });
  } finally {
    // Revoke on the next tick: the bitmap is decoded by then and drawImage
    // does not need the URL any more.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
import type { PaintShapeTool } from './PaintTool';

const HISTORY_MAX = 40;

export interface PaintCanvasHandle {
  undo(): void;
  redo(): void;
  clear(): void;
  isEmpty(): boolean;
  /** Base image + drawing overlay → PNG blob (null when nothing drawn). */
  /**
   * Composite base + annotations. `baseIncluded=false` means the base image
   * could not be fetched and only the annotation layer is in `blob` —
   * callers must not present that as the finished picture.
   */
  exportComposite(): Promise<{ blob: Blob | null; baseIncluded: boolean }>;
}

function circledNumber(n: number): string {
  return n >= 1 && n <= 20 ? String.fromCharCode(0x2460 + n - 1) : String(n);
}

export const PaintCanvas = forwardRef<
  PaintCanvasHandle,
  {
    src: string;
    alt?: string;
    tool: PaintShapeTool;
    color: string;
    size: number;
    onHistoryChange?: (canUndo: boolean, canRedo: boolean) => void;
  }
>(function PaintCanvas({ src, alt = '', tool, color, size, onHistoryChange }, ref) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  // The display image is a plain <img>: showing a picture needs no pixel
  // access, so it must not depend on CORS. The previous design loaded it with
  // crossOrigin so the canvas could read it back — and one intermittent 502
  // on an Origin-bearing request (measured on prod, 2026-08-29) flipped the
  // component into "overlay only" for good, with a visible flash as the
  // element re-mounted. Export fetches its own bytes instead; see below.
  const imgRef = useRef<HTMLImageElement | null>(null);
  const undoStack = useRef<ImageData[]>([]);
  const redoStack = useRef<ImageData[]>([]);
  const labelCounter = useRef(1);
  const drawState = useRef<{
    x: number;
    y: number;
    sx: number;
    sy: number;
    snapshot: ImageData | null;
  } | null>(null);
  // Live knobs in refs so pointer handlers never see stale props.
  const knobs = useRef({ tool, color, size });
  knobs.current = { tool, color, size };
  const notify = () =>
    onHistoryChange?.(undoStack.current.length > 0, redoStack.current.length > 0);

  const ctx2d = () => canvasRef.current?.getContext('2d') ?? null;

  const snapshot = (): ImageData | null => {
    const c = canvasRef.current;
    const ctx = ctx2d();
    if (!c || !ctx || !c.width) return null;
    try {
      return ctx.getImageData(0, 0, c.width, c.height);
    } catch {
      return null;
    }
  };
  const restore = (snap: ImageData | null) => {
    const ctx = ctx2d();
    if (!ctx || !snap) return;
    ctx.putImageData(snap, 0, 0);
  };
  const pushHistory = () => {
    const snap = snapshot();
    if (!snap) return;
    undoStack.current.push(snap);
    if (undoStack.current.length > HISTORY_MAX) undoStack.current.shift();
    redoStack.current = [];
    notify();
  };

  const setupStyle = (ctx: CanvasRenderingContext2D) => {
    const k = knobs.current;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.lineWidth = k.size;
    ctx.strokeStyle = k.color;
    ctx.fillStyle = k.color;
    ctx.globalCompositeOperation = 'source-over';
  };

  const pointOf = (e: { clientX: number; clientY: number }) => {
    const c = canvasRef.current!;
    const rect = c.getBoundingClientRect();
    return {
      x: ((e.clientX - rect.left) * c.width) / Math.max(1, rect.width),
      y: ((e.clientY - rect.top) * c.height) / Math.max(1, rect.height),
    };
  };

  const strokeFreePoint = (p: { x: number; y: number }) => {
    const st = drawState.current;
    const ctx = ctx2d();
    if (!st || !ctx) return;
    setupStyle(ctx);
    const dx = p.x - st.x;
    const dy = p.y - st.y;
    const dist = Math.hypot(dx, dy);
    const radius = Math.max(1, knobs.current.size / 2);
    if (dist > radius) {
      const steps = Math.ceil(dist / Math.max(1, radius * 0.35));
      for (let i = 1; i <= steps; i++) {
        const t = i / steps;
        ctx.beginPath();
        ctx.arc(st.x + dx * t, st.y + dy * t, radius, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.beginPath();
    ctx.moveTo(st.x, st.y);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    st.x = p.x;
    st.y = p.y;
  };

  const drawShape = (
    ctx: CanvasRenderingContext2D,
    start: { x: number; y: number },
    end: { x: number; y: number },
  ) => {
    setupStyle(ctx);
    const x = Math.min(start.x, end.x);
    const y = Math.min(start.y, end.y);
    const w = Math.abs(end.x - start.x);
    const h = Math.abs(end.y - start.y);
    if (knobs.current.tool === 'rect') ctx.strokeRect(x, y, w, h);
    else if (knobs.current.tool === 'ellipse') {
      ctx.beginPath();
      ctx.ellipse(x + w / 2, y + h / 2, Math.max(1, w / 2), Math.max(1, h / 2), 0, 0, Math.PI * 2);
      ctx.stroke();
    }
  };

  const drawLabel = (p: { x: number; y: number }) => {
    const ctx = ctx2d();
    if (!ctx) return;
    const fontSize = Math.max(18, knobs.current.size * 2.2);
    const text = circledNumber(labelCounter.current++);
    setupStyle(ctx);
    ctx.save();
    ctx.font = `900 ${fontSize}px Arial, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.lineWidth = Math.max(3, fontSize / 8);
    ctx.strokeStyle = 'rgba(255,255,255,0.92)';
    ctx.strokeText(text, p.x, p.y);
    ctx.fillStyle = knobs.current.color;
    ctx.fillText(text, p.x, p.y);
    ctx.restore();
  };

  const drawText = (p: { x: number; y: number }) => {
    const ctx = ctx2d();
    if (!ctx) return;
    // eslint-disable-next-line no-alert
    const value = window.prompt('Text');
    if (!value) return;
    const fontSize = Math.max(18, knobs.current.size * 2.2);
    setupStyle(ctx);
    ctx.save();
    ctx.font = `700 ${fontSize}px Arial, sans-serif`;
    ctx.textBaseline = 'middle';
    ctx.fillStyle = knobs.current.color;
    ctx.fillText(value, p.x, p.y);
    ctx.restore();
  };

  const onPointerDown = (e: React.PointerEvent) => {
    // IC beginEditDraw: isolate from everything else FIRST.
    e.preventDefault();
    e.stopPropagation();
    const c = canvasRef.current;
    const ctx = ctx2d();
    if (!c || !ctx || !c.width) return;
    try {
      c.setPointerCapture?.(e.pointerId);
    } catch {
      /* non-capturable pointer — stroke works uncaptured */
    }
    const p = pointOf(e);
    pushHistory();
    const k = knobs.current;
    if (k.tool === 'label') {
      drawLabel(p);
      drawState.current = null;
      return;
    }
    if (k.tool === 'text') {
      drawText(p);
      drawState.current = null;
      return;
    }
    drawState.current = {
      x: p.x,
      y: p.y,
      sx: p.x,
      sy: p.y,
      snapshot: k.tool !== 'free' ? snapshot() : null,
    };
    setupStyle(ctx);
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
    ctx.lineTo(p.x + 0.01, p.y + 0.01);
    if (k.tool === 'free') ctx.stroke();
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const st = drawState.current;
    if (!st) return;
    e.preventDefault();
    e.stopPropagation();
    const ctx = ctx2d();
    if (!ctx) return;
    if (knobs.current.tool !== 'free') {
      restore(st.snapshot);
      drawShape(ctx, { x: st.sx, y: st.sy }, pointOf(e));
      return;
    }
    const native = e.nativeEvent as PointerEvent;
    const coalesced =
      typeof native.getCoalescedEvents === 'function'
        ? native.getCoalescedEvents()
        : [];
    if (coalesced.length) {
      for (const ev of coalesced) strokeFreePoint(pointOf(ev));
    } else {
      strokeFreePoint(pointOf(e));
    }
  };

  const endStroke = (e: React.PointerEvent) => {
    if (drawState.current) {
      try {
        canvasRef.current?.releasePointerCapture?.(e.pointerId);
      } catch {
        /* already released */
      }
    }
    drawState.current = null;
  };

  useImperativeHandle(ref, () => ({
    undo() {
      const prev = undoStack.current.pop();
      if (!prev) return;
      const cur = snapshot();
      if (cur) redoStack.current.push(cur);
      restore(prev);
      notify();
    },
    redo() {
      const next = redoStack.current.pop();
      if (!next) return;
      const cur = snapshot();
      if (cur) undoStack.current.push(cur);
      restore(next);
      notify();
    },
    clear() {
      const c = canvasRef.current;
      const ctx = ctx2d();
      if (!c || !ctx) return;
      pushHistory();
      ctx.clearRect(0, 0, c.width, c.height);
    },
    isEmpty() {
      return undoStack.current.length === 0;
    },
    async exportComposite() {
      const c = canvasRef.current;
      if (!c || !c.width) return { blob: null, baseIncluded: false };
      const out = document.createElement('canvas');
      out.width = c.width;
      out.height = c.height;
      const ctx = out.getContext('2d');
      if (!ctx) return { blob: null, baseIncluded: false };

      // Fetch the base through the API client rather than reading the
      // display <img>: that goes through auth and the dual-channel failover,
      // and a blob URL is same-origin, so drawing it never taints the canvas.
      // One retry: the failure this guards against is an edge blip, not a
      // dead resource.
      const base = await fetchBaseBitmap(src);
      if (base) {
        ctx.drawImage(base, 0, 0, out.width, out.height);
      } else {
        console.warn('[paint] base image could not be fetched; exporting annotations only');
      }
      ctx.drawImage(c, 0, 0);
      const blob = await new Promise<Blob | null>((resolve) => {
        try {
          out.toBlob(resolve, 'image/png');
        } catch (err) {
          console.error('[paint] export failed', err);
          resolve(null);
        }
      });
      return { blob, baseIncluded: base !== null };
    },
  }));

  // Size the overlay to the image's natural pixels once loaded.
  useEffect(() => {
    undoStack.current = [];
    redoStack.current = [];
    labelCounter.current = 1;
    notify();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src]);

  return (
    <div className="relative inline-block max-h-full max-w-full select-none">
      <img
        ref={imgRef}
        src={mediaSrc(src)}
        alt={alt}
        draggable={false}
        className="pointer-events-none block max-h-[62vh] max-w-full object-contain"
        onLoad={(e) => {
          const img = e.currentTarget;
          const c = canvasRef.current;
          if (c && img.naturalWidth) {
            c.width = img.naturalWidth;
            c.height = img.naturalHeight;
          }
        }}
      />
      <canvas
        ref={canvasRef}
        data-testid="paint-canvas"
        className="absolute inset-0 h-full w-full cursor-crosshair touch-none"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endStroke}
        onPointerCancel={endStroke}
        onPointerLeave={endStroke}
      />
    </div>
  );
});
