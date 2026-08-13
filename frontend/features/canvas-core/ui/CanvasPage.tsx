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
 *
 * `CanvasView` (below) is the actual work component — extracted from this
 * route's body (shot-nodes-on-canvas Task 5) so the storyboard page's
 * "Canvas" tab can mount the SAME reconcile/focus/composer orchestration
 * embedded (by `canvasId` prop) without going through React Router at all.
 * `CanvasPage` (default export, unchanged route behaviour) is now a thin
 * shell: it resolves `canvasId`/`teamId` from the URL and a `onBack`
 * handler from history, then delegates to `CanvasView`. The route-only
 * bits (missing-canvasId guard, the "back to canvases" pill's history
 * logic) stay here since an embedded mount has neither concept — passing
 * no `onBack` simply hides the pill (see its own comment below).
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowLeft } from 'lucide-react';
import type { ReactFlowInstance } from '@xyflow/react';

import { CommandPalette } from '../palette/CommandPalette';
import { CanvasComposer } from '../smart/CanvasComposer';
import { buildCharacterTemplate } from '../smart/characterTemplate';
import { buildEntityTemplate } from '../smart/entityTemplates';
import { resumePendingGenerations } from '../smart/genResume';
import { computeShotLabel, reconcileShotNodes } from '../smart/shotSync';
import { onPromoteShot } from '../smart/promoteShotBus';
import { PromoteShotDialog } from '../smart/PromoteShotDialog';
import type { ShotNodeData, SmartNode } from '../smart/types';
import { isSmartFamily } from '../types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { viewportFramesAnyNode } from '../utils/viewport';
import { useCanvasRealtime } from '../realtime/useCanvasRealtime';
import { fetchScriptProjects } from '../../../services/scriptService';
import { listScenes, listShots, createShot } from '../../../editor/sceneService';
import type { SceneDoc } from '../../../editor/types';
import { CanvasConflictDialog } from './CanvasConflictDialog';
import { CanvasSurface } from './CanvasSurface';
import { TopNodeBar } from './TopNodeBar';
import { ArrangeSelectedButton } from './ArrangeSelectedButton';
import { ShortcutHelpPanel } from './ShortcutHelpPanel';
import { useCanvasShortcuts } from './useCanvasShortcuts';

export interface CanvasViewProps {
  canvasId: string;
  /** Only used for the "back to canvases" pill's list-route fallback and
   *  the CanvasComposer's team-scoped affordances — both irrelevant when
   *  embedded (the storyboard page has its own tabs, no composer renders
   *  for `kind==='storyboard'` either way, see `isSmartFamily` below). */
  teamId?: string;
  /** Renders the "back to canvases" pill when provided; omitted entirely
   *  in embedded mode (the storyboard page's own tabs are the way back —
   *  a second, route-shaped "back" pill inside an embedded tab would be
   *  confusing chrome pointing at the standalone canvas list). */
  onBack?: () => void;
  /**
   * Shot to bring into view (shot-nodes-on-canvas Task 5): the three focus
   * entry points (a shot card click, the `?view=canvas&shot=` URL deep
   * link, `shotFocusBus`) all converge on this single prop by the time they
   * reach this component. Only meaningful for `kind==='storyboard'`
   * canvases — every other kind simply never has a matching `shot-{id}`
   * node, so the effect below silently no-ops.
   */
  focusShotId?: string | null;
  /**
   * Fires once a `focusShotId` request has settled (node found and
   * fitView'd, OR not found — same fire-and-forget philosophy as the focus
   * buses this feature converges: "a chip that does nothing beats a crash").
   * Lets the caller clear a one-shot trigger (e.g. the URL's `shot=` query
   * param) without this component owning any URL/state concerns itself.
   */
  onFocusHandled?: () => void;
  /**
   * Bump to force the shot-node reconcile effect below to re-run without a
   * full remount (Task 6 review 修复轮1, 2026-08-11): an Agent Run Undo
   * (`useRunUndo.ts`'s `requestStoryboardRefresh`) can delete/roll back
   * `script_shots` rows out from under an ALREADY-mounted storyboard canvas
   * — the old editor storyboard rail's `StoryboardView` had its own
   * `onStoryboardRefresh` subscriber for this; the canvas never needed one
   * before because Task 4/5 only ever reconciled once, on mount. Any
   * change (an incrementing counter is simplest — the effect only cares
   * that the value differs from last render, not its magnitude) re-runs the
   * EXACT SAME reconcile fetch+diff below; this is not a second mechanism,
   * just one more trigger on the existing one. `undefined`/omitted never
   * fires it (every other caller — the standalone `/canvas/:id` route via
   * `CanvasPage` below, and any embed that doesn't care — is unaffected).
   */
  reconcileRefreshToken?: number;
}

/**
 * The actual canvas work surface — see the file doc comment above for why
 * this is split from the default-exported route component.
 */
export function CanvasView({
  canvasId,
  teamId,
  onBack,
  focusShotId = null,
  onFocusHandled,
  reconcileRefreshToken,
}: CanvasViewProps) {
  const surfaceRef = useRef<HTMLDivElement>(null);
  const loadStatus = useCanvasCoreStore((s) => s.loadStatus);
  const loadError = useCanvasCoreStore((s) => s.loadError);
  const saveStatus = useCanvasCoreStore((s) => s.saveStatus);
  const saveError = useCanvasCoreStore((s) => s.saveError);
  const readOnly = useCanvasCoreStore((s) => s.readOnly);
  const kind = useCanvasCoreStore((s) => s.kind);
  const name = useCanvasCoreStore((s) => s.name);
  const projectId = useCanvasCoreStore((s) => s.projectId);
  const episodeId = useCanvasCoreStore((s) => s.episodeId);
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
    // Mutating chords (delete / paste / duplicate / group / undo / knife)
    // are dropped for a read-only session; palette, help, copy, select-all
    // and Esc stay — see the hook's own `readOnly` doc.
    readOnly,
    onOpenPalette: () => setPaletteOpen(true),
    onOpenHelp: () => setHelpOpen(true),
  });

  // Phase 6a — cross-tab / cross-user realtime invalidation.
  // When another session saves a newer revision, applyRemoteUpdate in the
  // store either rebases (clean local state) or surfaces a conflict (dirty
  // edits) without clobbering.
  useCanvasRealtime(canvasId);

  useEffect(() => {
    void loadCanvas(canvasId);
    // Generation guard (Task 5 评审修复轮1 — stale-unmount-reset race):
    // `loadCanvas`'s synchronous prefix (before its first `await`) has
    // already bumped `mountEpoch` by the time this line runs, so capturing
    // it here reflects THIS mount's generation. Embedding this view in a
    // frequently-toggled tab (the storyboard page's Canvas tab) turned a
    // rare route-navigation unmount into a high-frequency one: a fast
    // tab-away-then-back can mount a NEW instance (bumping `mountEpoch`
    // again) before THIS instance's own `flushSave()` tail below resolves.
    // Unguarded, that stale `reset()` would slam the NEW instance's
    // already-`ready` state back to `idle` with nobody left to re-trigger
    // `loadCanvas` — permanently stuck on "Loading canvas…".
    const myEpoch = useCanvasCoreStore.getState().mountEpoch;
    return () => {
      // Flush ALWAYS runs (dirty edits from before the switch must not be
      // dropped) — but only reset the store if no newer mount has taken
      // over lifecycle ownership since this one started.
      void flushSave().finally(() => {
        if (useCanvasCoreStore.getState().mountEpoch === myEpoch) {
          reset();
        }
      });
    };
  }, [canvasId, loadCanvas, flushSave, reset]);

  // Shot-node reconcile (shot-nodes-on-canvas epic Task 4 — shotSync.ts):
  // for a storyboard canvas (kind==='storyboard', mig 421), sync the shot
  // nodes against the episode's current script_shots BEFORE the resume
  // effect below runs. Ordering rationale: reconcile is what determines the
  // FINAL node set (adds missing shots, flags deleted ones stale) — resume
  // only re-attaches polling for nodes that ALREADY carry a `gen_task_id`,
  // which reconcile never sets (new nodes always start with
  // `gen_task_id: null`) and never clears on an in-flight node (the
  // in-flight guard in `reconcileShotNodes`). So there's no real ordering
  // HAZARD either way, but declaring reconcile first keeps "the node set is
  // settled, then generation state is resumed" the readable story, matching
  // how the two concerns are already separated in `ShotNodeView.tsx`.
  //
  // Script resolution is READ-ONLY (mirrors `ProjectWorkspace.tsx`'s
  // `findExistingScript` — filters `fetchScriptProjects(projectId)` by
  // `episode_id` client-side, same pagination-page-1 limitation as that
  // established precedent) — a storyboard canvas opened before the user
  // ever clicked "Start Storyboard" has no script yet, and reconcile must
  // NOT silently provision one just because the canvas mounted.
  //
  // Fetches live in this page component rather than in shotSync.ts itself:
  // shotSync's `reconcileShotNodes` is a pure function (no store/service
  // imports, exhaustively unit-tested on its own) — the store has no
  // natural place for an async multi-request orchestration either
  // (`loadCanvas` is the row fetch, not a place to bolt on a second
  // subsystem's fetch fan-out), so the impure "fetch scenes/shots, apply
  // the diff" glue lives here instead, in the one place that already owns
  // canvas mount lifecycle.
  //
  // `reconcileRefreshToken` (Task 6 review 修复轮1) rides in this same
  // effect's dep array purely to force a re-run — see its own doc comment
  // on `CanvasViewProps` for why (an Agent Run Undo mutating script_shots
  // while this canvas is already mounted).
  const promoteScenesRef = useRef<SceneDoc[]>([]);
  const [promoteScenes, setPromoteScenes] = useState<SceneDoc[]>([]);

  // Reconcile settledness (Task 5 — gates the focus effect below): a shot
  // that reconcile is ABOUT to auto-add isn't in `store.nodes` yet, so
  // focusing before reconcile settles would silently miss it. Reset
  // whenever the canvas identity changes so a stale "done" from the
  // PREVIOUS canvas can't let a focus request jump the gun on this one —
  // see the ordering note on the two effects below for why this is safe
  // against the async reconcile's own in-flight cancellation.
  const [reconcileDone, setReconcileDone] = useState(false);
  useEffect(() => {
    setReconcileDone(false);
  }, [canvasId]);

  useEffect(() => {
    if (loadStatus !== 'ready') return;
    if (kind !== 'storyboard') {
      // No reconcile subsystem for any other kind — nothing to wait for,
      // the focus effect can proceed as soon as the surface is ready.
      setReconcileDone(true);
      return;
    }
    if (!projectId || !episodeId || !canvasId) return;
    let cancelled = false;
    const sameCanvas = () => useCanvasCoreStore.getState().canvasId === canvasId;

    void (async () => {
      try {
        const { data: scriptSummaries } = await fetchScriptProjects(projectId);
        const scriptId = scriptSummaries
          .filter((s) => String(s.episode_id ?? '') === String(episodeId))
          .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())[0]
          ?.id;
        if (!scriptId || cancelled || !sameCanvas()) return;

        const sceneDocs = await listScenes(scriptId);
        if (cancelled || !sameCanvas()) return;
        promoteScenesRef.current = sceneDocs;
        setPromoteScenes(sceneDocs);
        const scenes = sceneDocs.map((s, idx) => ({ id: s.id, sceneNo: idx + 1 }));

        const shotLists = await Promise.all(sceneDocs.map((s) => listShots(s.id)));
        if (cancelled || !sameCanvas()) return;
        const shots = shotLists.flat();

        const store = useCanvasCoreStore.getState();
        // `nodes_json` is opaque `CanvasNode[]` (Record<string, unknown>,
        // JSONB pass-through) at the store boundary — every real node DOES
        // carry id/type/position/data at runtime (the surface renders off
        // exactly that shape), so this narrows the same way every node
        // renderer's `NodeProps` already assumes.
        // `Shot` (sceneService.ts) has every field `reconcileShotNodes` reads
        // by name (id/scene_id/shot_type/…) but, being a named interface
        // rather than an index signature, isn't structurally assignable to
        // the brief's verbatim `[k: string]: unknown` shot-row shape without
        // this bridge — same class of cast as `SmartNode[]` above.
        const result = reconcileShotNodes(
          store.nodes as SmartNode[],
          scenes,
          shots as unknown as Array<{ id: string; scene_id: string; [k: string]: unknown }>,
        );
        if (cancelled || !sameCanvas()) return;
        // Self-heal FIRST (2026-08-12 incident): collapse duplicated node
        // ids down to their first occurrence before adds/patches — the
        // patches below address nodes by id, so they land on the survivor.
        // (Load already dedupes via `applyServerRow`; this channel covers
        // anything that slipped in after load.)
        if (result.nodeIdsToDedupe.length > 0) {
          store.dedupeNodes(result.nodeIdsToDedupe);
        }
        if (result.nodesToAdd.length > 0) {
          store.appendElementsNoHistory(result.nodesToAdd, []);
        }
        for (const p of result.nodesToPatch) {
          store.patchNode(p.id, { data: p.data });
        }
        for (const staleId of result.nodesToMarkStale) {
          store.patchNode(staleId, { data: { stale: true } });
        }
      } catch (err) {
        console.error('[CanvasPage] shot reconcile failed:', err);
      } finally {
        // Runs on every path out of `try` (early returns included) —
        // "reconcile settled" doesn't require anything to have actually
        // changed (e.g. no script yet is a legitimate settled state, the
        // focus effect below will just find no matching node and no-op).
        if (!cancelled) setReconcileDone(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [loadStatus, kind, canvasId, projectId, episodeId, reconcileRefreshToken]);

  // "Promote to Shot" (Task 4 — promoteShotBus's subscriber): only mounted
  // for a storyboard canvas, where a scene list is resolvable at all. Every
  // other canvas kind leaves the bus with no listener, which
  // `requestPromoteShot` already documents as a safe no-op.
  const [promoteNodeId, setPromoteNodeId] = useState<string | null>(null);
  const [promoteSubmitting, setPromoteSubmitting] = useState(false);
  const [promoteError, setPromoteError] = useState<string | null>(null);
  // Structural re-entrancy guard (review fix round 1): `promoteSubmitting`
  // already disables the dialog's scene buttons, but that's a STATE flag —
  // two clicks landing in the same tick (before React commits the re-render
  // that flips `disabled`) would both pass the `!submitting` check and both
  // call `createShot`. A synchronous ref can't have that race; checked and
  // set before the first `await`, same tick as the click handler runs.
  const promoteInFlightRef = useRef(false);

  useEffect(() => {
    if (kind !== 'storyboard') return;
    return onPromoteShot((nodeId) => {
      setPromoteError(null);
      setPromoteNodeId(nodeId);
    });
  }, [kind]);

  const handlePromoteCancel = useCallback(() => {
    setPromoteNodeId(null);
    setPromoteError(null);
  }, []);

  const handlePromotePickScene = useCallback(
    async (sceneId: string) => {
      if (!promoteNodeId || promoteInFlightRef.current) return;
      promoteInFlightRef.current = true;
      setPromoteSubmitting(true);
      setPromoteError(null);
      try {
        const created = await createShot(sceneId, {});
        // createShot's response has no ready-made index/label — recompute
        // the "1A" code against the scene's shot list AS IT NOW STANDS
        // (the new row is in it), the same convention `reconcileShotNodes`
        // uses for every other shot.
        const sceneShots = await listShots(sceneId);
        const idxInScene = Math.max(
          0,
          sceneShots.findIndex((s) => s.id === created.id),
        );
        const sceneIdx = Math.max(
          0,
          promoteScenesRef.current.findIndex((s) => s.id === sceneId),
        );
        const patch: Partial<ShotNodeData> = {
          shot_id: created.id,
          scene_id: sceneId,
          shot_label: computeShotLabel(sceneIdx + 1, idxInScene),
          shot_type: created.shot_type,
          camera_angle: created.camera_angle,
          camera_movement: created.camera_movement,
          focal_length: created.focal_length,
          description: created.description,
          image_url: created.image_url,
          shot_status: created.status,
          gen_task_id: null,
        };
        useCanvasCoreStore.getState().patchNode(promoteNodeId, { data: patch });
        setPromoteNodeId(null);
      } catch (err) {
        console.error('[CanvasPage] promote to shot failed:', err);
        setPromoteError(t('canvas.shotNode.promoteDialog.failed'));
      } finally {
        promoteInFlightRef.current = false;
        setPromoteSubmitting(false);
      }
    },
    [promoteNodeId, t],
  );

  // Broken-connection resume (P1-13 — Infinite's resumeSmartPendingTasks):
  // once the document is in, re-attach polling for any generation batch
  // that was in flight when the previous page died, and reset prompts
  // stranded in queued/running with nothing to resume.
  useEffect(() => {
    // 'storyboard' is deliberately excluded from `isSmartFamily` (T4/T5) so
    // it never grows composer/Arrange chrome — but it DOES render bound shot
    // nodes with their own generate/poll lifecycle (Task 3), so the resume
    // sweep still needs to run there. Checked as an explicit `|| kind ===
    // 'storyboard'` rather than folding it into `isSmartFamily` itself,
    // which would wrongly light up the smart-family UI surface too (final
    // review Critical 1 — this bypass previously made every in-flight shot
    // generation on the storyboard canvas immortally stuck on
    // "Generating…" after any refresh/reopen).
    if (loadStatus !== 'ready' || !(isSmartFamily(kind) || kind === 'storyboard')) return;
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

  // React Flow imperative instance (Task 5 — viewport focus). `rfReady` is a
  // reactive twin of the ref: React Flow's own `onInit` timing relative to
  // this component's other effects isn't a contract this file wants to
  // depend on, so the focus effect below re-evaluates explicitly whenever
  // the instance becomes available, instead of assuming it's already there
  // by the time `loadStatus`/`reconcileDone` flip.
  const rfInstanceRef = useRef<ReactFlowInstance | null>(null);
  const [rfReady, setRfReady] = useState(false);
  const handleCanvasInit = useCallback((instance: ReactFlowInstance) => {
    rfInstanceRef.current = instance;
    setRfReady(true);
  }, []);

  // Empty-viewport self-heal (2026-08-12 production incident, canvas
  // 337610660408263): that row's saved `viewport_json`
  // ({x:181.47,y:106.68,zoom:0.514}) frames world x≈-352..2138 while all six
  // shot nodes sit at x=2240 — and `CanvasEngine` runs with
  // `onlyRenderVisibleElements`, so off-screen nodes never enter the DOM at
  // all. The user opens the canvas and sees a blank grid; only the minimap
  // hints anything exists. A saved viewport that frames NOTHING carries no
  // information worth preserving, so we fit once instead of honouring it.
  //
  // Runs here rather than in the store: it needs both the rendered surface's
  // pixel size and React Flow's imperative instance, and it is purely a
  // VISUAL correction — going through the store would route it into the
  // viewport dirty channel (`setViewport` → `markDirty`) and persist a
  // viewport the user never chose. `instance.fitView()` moves React Flow's
  // own transform; the store's `viewport` follows via `onMove` →
  // `setViewportOnMove`, which by design does NOT bump revision (only the
  // RAF `flushViewportDirty` does, and no user gesture fired here).
  //
  // One shot per canvas id, and only once nodes exist: a storyboard canvas
  // whose shot nodes arrive from the Task 4 reconcile a tick after load gets
  // the same protection (the effect simply hasn't armed yet while
  // `nodeCount === 0`). An unmeasurable surface (0×0 — hidden tab, layout
  // not settled) means we cannot know what is framed, so we skip WITHOUT
  // latching and re-evaluate on the next render that changes the node list.
  const viewportHealedForRef = useRef<string | null>(null);
  useEffect(() => {
    if (loadStatus !== 'ready' || !canvasId) return;
    if (viewportHealedForRef.current === canvasId) return;
    if (nodeCount === 0) return;
    const instance = rfInstanceRef.current;
    const el = surfaceRef.current;
    if (!instance || !el) return;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    viewportHealedForRef.current = canvasId;
    const { viewport, nodes } = useCanvasCoreStore.getState();
    if (viewportFramesAnyNode(viewport, nodes, { width: rect.width, height: rect.height })) {
      return;
    }
    console.warn(
      '[CanvasView] saved viewport frames no node — fitting view instead',
      { canvasId, viewport, nodeCount },
    );
    instance.fitView({ padding: 0.2 });
  }, [loadStatus, canvasId, nodeCount, rfReady]);

  // Viewport focus (Task 5 — the three entry points: a shot card click, the
  // `?view=canvas&shot=` URL deep link, and `shotFocusBus`, all converge on
  // the `focusShotId` prop by the time they reach this component). Gated on
  // `reconcileDone` — see that state's own comment for why a shot that
  // reconcile is about to add must not be searched for before it lands.
  useEffect(() => {
    if (!focusShotId) return;
    if (loadStatus !== 'ready' || !reconcileDone) return;
    const instance = rfInstanceRef.current;
    if (!instance) return;
    const targetId = `shot-${focusShotId}`;
    const exists = useCanvasCoreStore
      .getState()
      .nodes.some((n) => (n as Record<string, unknown>).id === targetId);
    if (exists) {
      instance.fitView({ nodes: [{ id: targetId }], duration: 400, padding: 0.35 });
    }
    // Fires whether the node was found or not (fire-and-forget philosophy
    // shared with `shotFocusBus`/`openShotInListBus`: "a chip that does
    // nothing beats yanking the writer somewhere unexpected") — lets the
    // caller clear a one-shot trigger either way.
    onFocusHandled?.();
  }, [focusShotId, loadStatus, reconcileDone, rfReady, onFocusHandled]);

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

  // Classic (canvas 1.0 engine) is retired: existing rows are soft-deleted
  // by migration, but a stale deep link / trash restore can still land here.
  // Show a friendly notice instead of rendering a surface with no engine.
  if (kind === 'classic') {
    return (
      <CanvasStatus
        title={t('canvas.classicRetired', 'Classic canvases have been retired')}
        tone="info"
      />
    );
  }

  // The smart family renders the React Flow surface plus the node composer
  // palette overlay.
  return (
    <div ref={surfaceRef} className="relative h-full w-full">
      <CanvasSurface onInit={handleCanvasInit} />
      {/* Back-to-list pill + canvas name (Infinite parity) — top-left, above
          the surface. Only rendered for the real route (`onBack` provided);
          an embedded mount (the storyboard page's Canvas tab) has its own
          tabs as the way back, see `CanvasViewProps.onBack`'s doc comment. */}
      {onBack && (
        <div className="pointer-events-none absolute left-4 top-4 z-30 flex flex-col gap-1">
          <button
            type="button"
            onClick={onBack}
            className="canvas-island pointer-events-auto flex w-fit items-center gap-2 rounded-full px-3.5 py-2 text-xs font-medium text-ink-200 transition-colors hover:text-ink-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40"
          >
            <ArrowLeft size={14} />
            {t('canvas.backToList', 'Back to canvases')}
          </button>
          {name && (
            <div className="max-w-[16rem] truncate px-2 text-xs text-ink-500">{name}</div>
          )}
        </div>
      )}
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
      {/* Authoring chrome — every one of these three exists solely to WRITE
          (add a node, re-lay-out the selection, run a generation into the
          document), so a read-only session gets none of them. Unlike the
          palette (which greys its write commands out to stay a discoverable
          index of what a canvas can do), these are pure action affordances:
          a disabled-but-present toolbar would just be furniture. The
          "Read-only" badge is what explains their absence. */}
      {kind === 'smart' && !readOnly && <TopNodeBar surfaceRef={surfaceRef} />}
      {isSmartFamily(kind) && !readOnly && <ArrangeSelectedButton />}
      {isSmartFamily(kind) && !readOnly && (
        <CanvasComposer surfaceRef={surfaceRef} teamId={teamId} />
      )}
      <CanvasConflictDialog />
      <SaveBadge status={saveStatus} error={saveError} readOnly={readOnly} t={t} />
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
      />
      <ShortcutHelpPanel open={helpOpen} onClose={() => setHelpOpen(false)} />
      {kind === 'storyboard' && (
        <PromoteShotDialog
          open={promoteNodeId !== null}
          scenes={promoteScenes}
          // T4 forward note (Task 5): `reconcileDone` is the SAME settledness
          // signal the viewport-focus effect above gates on — an empty
          // `promoteScenes` before reconcile settles means "don't know yet",
          // not "this episode genuinely has no scenes".
          scenesLoading={!reconcileDone}
          submitting={promoteSubmitting}
          error={promoteError}
          onCancel={handlePromoteCancel}
          onPickScene={handlePromotePickScene}
        />
      )}
    </div>
  );
}

/** Route shell — see the file doc comment above. */
export default function CanvasPage() {
  const { canvasId, teamId } = useParams<{ canvasId: string; teamId?: string }>();
  const navigate = useNavigate();

  // Back to the canvas list (Infinite's 返回画布列表): prefer real history
  // (returns to whichever list the user came from — workspace module or the
  // landing page); a deep link with no in-app history falls back to the
  // team canvas list.
  const handleBack = useCallback(() => {
    const idx = (window.history.state as { idx?: number } | null)?.idx ?? 0;
    if (idx > 0) {
      navigate(-1);
    } else {
      navigate(teamId ? `/team/${teamId}/canvas` : '/', { replace: true });
    }
  }, [navigate, teamId]);

  if (!canvasId) {
    return <CanvasStatus title="Missing canvas id" tone="error" />;
  }

  return <CanvasView canvasId={canvasId} teamId={teamId} onBack={handleBack} />;
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
  readOnly,
  t,
}: {
  status: 'idle' | 'saving' | 'error';
  error: string | null;
  /** The server refused this session's writes — see the store's `readOnly`. */
  readOnly: boolean;
  t: (key: string, fallback: string) => string;
}) {
  // Read-only wins over every save state: once the latch is set no save can
  // be in flight or pending, so "Saving…"/"Save failed" would be describing a
  // channel that is closed. A viewer needs the standing fact, not an error.
  if (readOnly) {
    return (
      <div
        className="pointer-events-none absolute right-4 top-4 rounded-md bg-slate-900/80 px-3 py-1 text-xs font-medium text-white shadow"
        role="status"
        aria-live="polite"
      >
        {t('canvas.readOnly', 'Read-only')}
      </div>
    );
  }
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
