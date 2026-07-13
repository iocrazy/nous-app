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

import { useEffect, useRef, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { ClassicPalette } from '../classic/ui/ClassicPalette';
import { ClassicRunBar } from '../classic/ui/ClassicRunBar';
import { CommandPalette } from '../palette/CommandPalette';
import { CanvasComposer } from '../smart/CanvasComposer';
import { buildCharacterTemplate } from '../smart/characterTemplate';
import { buildEntityTemplate } from '../smart/entityTemplates';
import { resumePendingGenerations } from '../smart/genResume';
import { isSmartFamily } from '../types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { useCanvasRealtime } from '../realtime/useCanvasRealtime';
import { CanvasConflictDialog } from './CanvasConflictDialog';
import { CanvasSurface } from './CanvasSurface';
import { ShortcutHelpPanel } from './ShortcutHelpPanel';
import { useCanvasShortcuts } from './useCanvasShortcuts';

export default function CanvasPage() {
  const { canvasId, teamId } = useParams<{ canvasId: string; teamId?: string }>();
  const surfaceRef = useRef<HTMLDivElement>(null);
  const loadStatus = useCanvasCoreStore((s) => s.loadStatus);
  const loadError = useCanvasCoreStore((s) => s.loadError);
  const saveStatus = useCanvasCoreStore((s) => s.saveStatus);
  const saveError = useCanvasCoreStore((s) => s.saveError);
  const kind = useCanvasCoreStore((s) => s.kind);
  const nodeCount = useCanvasCoreStore((s) => s.nodes.length);
  const loadCanvas = useCanvasCoreStore((s) => s.loadCanvas);
  const flushSave = useCanvasCoreStore((s) => s.flushSave);
  const reset = useCanvasCoreStore((s) => s.reset);
  const { t } = useTranslation();

  // Cmd+K palette + ? help — canvas-only scope, active only when ready.
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);

  useCanvasShortcuts({
    enabled: loadStatus === 'ready',
    onOpenPalette: () => setPaletteOpen(true),
    onOpenHelp: () => setHelpOpen(true),
  });

  // Phase 6a — cross-tab / cross-user realtime invalidation.
  // When another session saves a newer revision, applyRemoteUpdate in the
  // store either rebases (clean local state) or surfaces a conflict (dirty
  // edits) without clobbering. canvasId is null before params resolve.
  useCanvasRealtime(canvasId);

  useEffect(() => {
    if (!canvasId) return;
    void loadCanvas(canvasId);
    return () => {
      // Flush before clearing; reset() drops the document and the timer.
      void flushSave().finally(reset);
    };
  }, [canvasId, loadCanvas, flushSave, reset]);

  // Broken-connection resume (P1-13 — Infinite's resumeSmartPendingTasks):
  // once the document is in, re-attach polling for any generation batch
  // that was in flight when the previous page died, and reset prompts
  // stranded in queued/running with nothing to resume.
  useEffect(() => {
    if (loadStatus !== 'ready' || !isSmartFamily(kind)) return;
    void resumePendingGenerations();
  }, [loadStatus, kind, canvasId]);

  // Character canvas preset workflow (PR-CC2): an EMPTY kind='character'
  // canvas seeds the bible-card + four agent branches exactly once. Guarded
  // by nodeCount===0 AND a per-canvas ref (StrictMode double-run), persisted
  // through the normal debounced save. ?name=&description=&characterId= from
  // the library's "Open in Canvas" pre-fill the card.
  const [searchParams] = useSearchParams();
  const seededRef = useRef<string | null>(null);
  useEffect(() => {
    const isEntityKind =
      kind === 'character' || kind === 'location' || kind === 'prop';
    if (loadStatus !== 'ready' || !isEntityKind) return;
    if (nodeCount > 0 || !canvasId || seededRef.current === canvasId) return;
    seededRef.current = canvasId;
    const name = searchParams.get('name') ?? undefined;
    const description = searchParams.get('description') ?? undefined;
    const { nodes, connections } =
      kind === 'character'
        ? buildCharacterTemplate({
            character_id: searchParams.get('characterId'),
            name,
            description,
          })
        : buildEntityTemplate(kind, {
            entity_id: searchParams.get('entityId'),
            name,
            description,
          });
    const store = useCanvasCoreStore.getState();
    store.setNodes(nodes);
    store.setConnections(connections);
  }, [loadStatus, kind, nodeCount, canvasId, searchParams]);

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
      {/* Empty-canvas hint floats OVER the live surface instead of replacing
          it: the palette/composer are the only way to add a first node, so a
          full-screen empty state would dead-end a freshly created canvas
          (New Canvas → navigate lands here with zero nodes). pointer-events
          stay off so the surface underneath keeps every interaction; the
          hint disappears with the first node. */}
      {nodeCount === 0 && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
          <div className="max-w-sm text-center text-slate-700 dark:text-slate-300">
            <p className="text-base font-medium">{t('canvas.empty.title')}</p>
            <p className="mt-1 text-xs opacity-70">{t('canvas.empty.hint')}</p>
          </div>
        </div>
      )}
      {isSmartFamily(kind) && <CanvasComposer surfaceRef={surfaceRef} teamId={teamId} />}
      {kind === 'classic' && (
        <>
          <ClassicPalette surfaceRef={surfaceRef} />
          <ClassicRunBar />
        </>
      )}
      <CanvasConflictDialog />
      <SaveBadge status={saveStatus} error={saveError} />
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
      />
      <ShortcutHelpPanel open={helpOpen} onClose={() => setHelpOpen(false)} />
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
