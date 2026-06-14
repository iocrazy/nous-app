/**
 * Canvas page (Phase 1 Week 2 integration point).
 *
 * Mounted at `/team/:teamId/canvas/:canvasId`. Loads the canvas via the
 * Zustand store on mount, renders the React Flow surface once ready,
 * and surfaces optimistic-lock conflicts via the dialog.
 *
 * Persistence (debounced 500ms save) is owned by the store; this
 * component only deals with lifecycle + status UI. On unmount we flush
 * any pending save so a navigation away doesn't drop the last 500ms of
 * edits.
 */

import { useEffect, useRef } from 'react';
import { useParams } from 'react-router-dom';

import { ClassicPalette } from '../classic/ui/ClassicPalette';
import { ClassicRunBar } from '../classic/ui/ClassicRunBar';
import { CanvasComposer } from '../smart/CanvasComposer';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { CanvasConflictDialog } from './CanvasConflictDialog';
import { CanvasSurface } from './CanvasSurface';
import { useCanvasShortcuts } from './useCanvasShortcuts';

export default function CanvasPage() {
  const { canvasId } = useParams<{ canvasId: string }>();
  const surfaceRef = useRef<HTMLDivElement>(null);
  const loadStatus = useCanvasCoreStore((s) => s.loadStatus);
  const loadError = useCanvasCoreStore((s) => s.loadError);
  const saveStatus = useCanvasCoreStore((s) => s.saveStatus);
  const saveError = useCanvasCoreStore((s) => s.saveError);
  const kind = useCanvasCoreStore((s) => s.kind);
  const loadCanvas = useCanvasCoreStore((s) => s.loadCanvas);
  const flushSave = useCanvasCoreStore((s) => s.flushSave);
  const reset = useCanvasCoreStore((s) => s.reset);

  useCanvasShortcuts({ enabled: loadStatus === 'ready' });

  useEffect(() => {
    if (!canvasId) return;
    void loadCanvas(canvasId);
    return () => {
      // Flush before clearing; reset() drops the document and the timer.
      void flushSave().finally(reset);
    };
  }, [canvasId, loadCanvas, flushSave, reset]);

  if (!canvasId) {
    return <CanvasStatus title="Missing canvas id" tone="error" />;
  }

  if (loadStatus === 'loading' || loadStatus === 'idle') {
    return <CanvasStatus title="Loading canvas…" tone="info" />;
  }

  if (loadStatus === 'error') {
    return (
      <CanvasStatus
        title="Failed to load canvas"
        tone="error"
        detail={loadError ?? undefined}
      />
    );
  }

  // Both modes render the same React Flow surface — CanvasSurface picks
  // the nodeTypes map (smart vs classic) off the store `kind`. The
  // per-mode overlays differ: SmartMode adds the node composer palette;
  // ClassicMode adds its own palette (top-left, to ADD nodes) plus the run
  // bar (bottom-center, to RUN the cascade) — the two overlays are placed
  // so they never overlap. Branch explicitly per mode.
  return (
    <div ref={surfaceRef} className="relative h-full w-full">
      <CanvasSurface />
      {kind === 'smart' && <CanvasComposer surfaceRef={surfaceRef} />}
      {kind === 'classic' && (
        <>
          <ClassicPalette surfaceRef={surfaceRef} />
          <ClassicRunBar />
        </>
      )}
      <CanvasConflictDialog />
      <SaveBadge status={saveStatus} error={saveError} />
    </div>
  );
}

function CanvasStatus({
  title,
  detail,
  tone,
}: {
  title: string;
  detail?: string;
  tone: 'info' | 'error';
}) {
  const toneClass =
    tone === 'error'
      ? 'text-rose-700 dark:text-rose-300'
      : 'text-slate-700 dark:text-slate-300';
  return (
    <div className="flex h-full w-full items-center justify-center">
      <div className={`text-center ${toneClass}`}>
        <p className="text-base font-medium">{title}</p>
        {detail ? <p className="mt-1 text-xs opacity-70">{detail}</p> : null}
      </div>
    </div>
  );
}

function SaveBadge({
  status,
  error,
}: {
  status: 'idle' | 'saving' | 'error';
  error: string | null;
}) {
  if (status === 'idle') return null;
  const label =
    status === 'saving'
      ? 'Saving…'
      : error === 'conflict'
        ? 'Conflict'
        : 'Save failed';
  const className =
    status === 'saving'
      ? 'bg-slate-900/80 text-white'
      : error === 'conflict'
        ? 'bg-amber-500 text-white'
        : 'bg-rose-600 text-white';
  return (
    <div
      className={`pointer-events-none absolute right-4 top-4 rounded-md px-3 py-1 text-xs font-medium shadow ${className}`}
      role="status"
      aria-live="polite"
    >
      {label}
    </div>
  );
}
