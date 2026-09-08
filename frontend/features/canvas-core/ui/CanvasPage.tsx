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

import { LibraryPanel } from '../library/LibraryPanel';
import { insetStyle, useLibraryInset } from '../library/libraryInset';
import { useLibraryStore } from '../library/libraryStore';
import { CommandPalette } from '../palette/CommandPalette';
import { CanvasComposer } from '../smart/CanvasComposer';
import { useCanvasScope } from '../smart/canvasScope';
import { createAssetNode } from '../smart/factories';
import {
  applyLegacyVerdicts,
  legacyCards,
  resolveLegacyVerdicts,
} from '../smart/legacyMigration';
import { resumePendingGenerations } from '../smart/genResume';
import { computeShotLabel, reconcileShotNodes } from '../smart/shotSync';
import { onPromoteShot } from '../smart/promoteShotBus';
import { PromoteShotDialog } from '../smart/PromoteShotDialog';
import type { ShotNodeData, SmartNode } from '../smart/types';
import type { CanvasNode } from '../types';
import { isEntityCanvas, isSmartFamily } from '../types';
import { useOptionalToast } from '../../../components/Toast';
import { fetchAssetDetail, resolveLegacyAsset } from '../../../services/assetsService';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { classifySaveFailure } from '../utils/saveFailure';
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
 * Zoom floor for the empty-viewport self-heal below (2026-08-13 user report:
 * "画布是缩小的，不是整域的" — six shot cards squeezed into a thin, unreadable
 * column with empty gutters either side).
 *
 * A bare `fitView` optimises for "every node on screen", which for the
 * COMMON storyboard shape is the wrong objective: `shotSync.ts` lays scenes
 * out as columns and shots as rows within a column, so a single-scene
 * episode is one tall-thin stack (6 shots ≈ 280 × 2360 world px). Fitting
 * that into a 1280×720 surface is height-driven and lands around zoom 0.2–0.3
 * (0.29 on the reported canvas). Everything is visible and nothing is
 * legible — the bound shot card's description textarea is `text-xs` (12px)
 * → 3.5 CSS px, its four vocabulary chips `text-[11px]` → 3.2, the
 * `shot_label` chip `text-[10px]` → 2.9. That is a thumbnail, not a
 * workspace.
 *
 * 0.7 is the smallest zoom at which a `SMART_NODE_DEFAULT_WIDTH.shot` (280) ×
 * `SHOT_SYNC_NODE_HEIGHT_ESTIMATE` (360) card still READS — every text run it
 * carries stays at or above 7 CSS px:
 *   description  12px × 0.7 = 8.4 CSS px  (the smallest type this design
 *                                          system ships anywhere is 9px;
 *                                          ~8px is the practical legibility
 *                                          floor for short on-screen runs)
 *   chips        11px × 0.7 = 7.7 CSS px
 *   shot label   10px × 0.7 = 7.0 CSS px
 * 0.5 was the first candidate and does not survive the same arithmetic: the
 * description lands at 6px and the label at 5px — shapes, not words.
 *
 * The trade-off is deliberate and one-directional: content that does not fit
 * at 0.7 OVERFLOWS the viewport (React Flow centres on the content's
 * midpoint) and the user pans to the rest. "Too big to fit, pan to see it"
 * is a canvas working normally; "all of it on screen, none of it readable"
 * is the bug being fixed here.
 *
 * Only the AUTOMATIC heal is floored. `CanvasSurface`'s own `minZoom={0.1}`
 * is untouched, so a user who deliberately wants the bird's-eye overview
 * still zooms out to it by hand.
 */
export const VIEWPORT_HEAL_MIN_ZOOM = 0.7;

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
  const saveErrorStatus = useCanvasCoreStore((s) => s.saveErrorStatus);
  const readOnly = useCanvasCoreStore((s) => s.readOnly);
  const kind = useCanvasCoreStore((s) => s.kind);
  const name = useCanvasCoreStore((s) => s.name);
  const projectId = useCanvasCoreStore((s) => s.projectId);
  const episodeId = useCanvasCoreStore((s) => s.episodeId);
  const assetId = useCanvasCoreStore((s) => s.assetId);
  const nodeCount = useCanvasCoreStore((s) => s.nodes.length);
  const loadCanvas = useCanvasCoreStore((s) => s.loadCanvas);
  const flushSave = useCanvasCoreStore((s) => s.flushSave);
  const reset = useCanvasCoreStore((s) => s.reset);
  const { t } = useTranslation();
  const toast = useOptionalToast();
  const [searchParams] = useSearchParams();
  // How much of this surface the Library panel is standing on. Read here,
  // above every early return, because it is published on the surface root
  // for EVERY kind — a board with no Library still has to define the
  // variables its islands read, or the fallback in each declaration becomes
  // the only thing holding the layout together.
  const inset = useLibraryInset();

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
    onToggleLibrary: isSmartFamily(kind) ? () => useLibraryStore.getState().toggle() : undefined, // `L` must not toggle a panel this kind never mounts (:841) — the module-singleton `open` would then leak into the next canvas.
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

  // ── Asset-bound seeding (P4 Task 6) ──────────────────────────────────────
  //
  // An EMPTY canvas that BELONGS to an asset (`canvases.asset_id`, set by the
  // sheet's "Open In Canvas") gets exactly one card: a reference to that asset.
  //
  // This replaces the `?characterId=` / `?entityId=` preset-workflow seeding
  // outright. That branch built a bible card plus four agent branches from the
  // query string, and NOTHING had produced those parameters since P3 Task 6
  // retired the old library pages — the canvas row's `asset_id` is where the
  // identity lives now, which is also why the seeded card is BOUND rather than
  // the unbound placeholder the old un-parameterised path produced.
  //
  // Latched per canvas (`seededRef`) against StrictMode's double-invoke, and
  // gated on `nodeCount === 0` so it can never touch a canvas with work on it.
  // The write goes through `setNodes`, i.e. the normal debounced save — a
  // seeded card the user reloads away from would otherwise be a canvas that
  // looks seeded and is not.
  const { scopeId: assetScopeId } = useCanvasScope();
  const seededRef = useRef<string | null>(null);
  // The reporter goes through a ref, and is NOT in the effect's deps.
  // `useTranslation`'s `t` and a context handle are both free to change
  // identity on any render; an effect that depended on either would re-run,
  // and its cleanup would set `cancelled = true` on the in-flight fetch — so
  // the seed would be requested over and over and applied never. Same reason
  // `useAssetFailureReporter` keeps a ref beside its callback.
  const reportSeedFailure = useCallback(() => {
    toast?.addToast(t('canvas.assetSeed.failed', 'Could not load this canvas asset'), 'error');
  }, [toast, t]);
  const reportSeedFailureRef = useRef(reportSeedFailure);
  reportSeedFailureRef.current = reportSeedFailure;
  useEffect(() => {
    if (loadStatus !== 'ready' || !canvasId || !assetId) return;
    if (nodeCount > 0 || seededRef.current === canvasId) return;
    if (!assetScopeId) {
      // No `/team/:teamId` segment means no asset scope to ask with, and an
      // empty `scope_id` is a 403, not an unscoped query. Not latched: the
      // route can still resolve (the bare paths redirect through the personal
      // team), and re-evaluating is free.
      return;
    }
    seededRef.current = canvasId;
    let cancelled = false;
    fetchAssetDetail(assetScopeId, assetId)
      .then((detail) => {
        if (cancelled) return;
        const store = useCanvasCoreStore.getState();
        // The canvas may have been swapped, or reconcile may have put nodes
        // here, while the detail was in flight. Seeding then would append a
        // card to a document that is no longer empty.
        if (store.canvasId !== canvasId || store.nodes.length > 0) return;
        store.setNodes([
          createAssetNode(detail, { position: { x: 0, y: 0 } }) as CanvasNode,
        ]);
      })
      .catch((err) => {
        // A blank board with no explanation is the silent no-op; say it.
        console.error('[CanvasView] could not seed the canvas asset:', err);
        if (!cancelled) reportSeedFailureRef.current();
      });
    return () => {
      cancelled = true;
    };
  }, [loadStatus, canvasId, assetId, assetScopeId, nodeCount]);

  // ── Legacy entity card migration (P4 Task 6) ─────────────────────────────
  //
  // Canvases saved before the asset library hold `character`/`location`/`prop`
  // cards keyed by `_legacy_project_*` row ids. Each one is resolved against
  // `GET /assets/resolve-legacy`; a hit becomes an asset card IN PLACE (same
  // node id, same position, edges untouched) and a miss gets a visible
  // `Unmigrated` badge. `legacyMigration.ts` owns every rule.
  //
  // It runs AFTER the document is in the store, never before: the legacy cards
  // paint immediately and swap when the answers arrive. And the answers are
  // applied to the LIVE node list, not the snapshot they were computed from,
  // so a drag during the round trip is not undone.
  //
  // `setNodesTransient` on purpose — no history entry (this is not a user
  // edit) and NO forced save. The rewritten nodes ride out with the next real
  // save; a PUT fired by merely opening a canvas would make every read of an
  // old board a write.
  const migratedForRef = useRef<string | null>(null);
  useEffect(() => {
    if (loadStatus !== 'ready' || !canvasId || !assetScopeId) return;
    if (migratedForRef.current === canvasId) return;
    const snapshot = useCanvasCoreStore.getState().nodes;
    if (legacyCards(snapshot).length === 0) return;
    migratedForRef.current = canvasId;
    let cancelled = false;
    // Did this pass reach a conclusion? A node added or deleted while the
    // resolves are out re-runs this effect (`nodeCount` is a dep), the cleanup
    // sets `cancelled`, and the re-run then returns early on the latch — so
    // the verdicts were thrown away and NOTHING would ever try again for this
    // mount. Self-healing across reloads (the design re-resolves on every load
    // until a save), but silent within one, which is the shape this branch
    // keeps filing bugs about. Releasing the latch when a pass applied nothing
    // lets the re-run pick the work back up.
    let applied = false;
    void resolveLegacyVerdicts(snapshot, {
      resolve: (kind, legacyId) => resolveLegacyAsset(assetScopeId, kind, legacyId),
      fetchDetail: (id) => fetchAssetDetail(assetScopeId, id),
    }).then((verdicts) => {
      if (cancelled || verdicts.length === 0) return;
      const store = useCanvasCoreStore.getState();
      if (store.canvasId !== canvasId) return;
      const outcome = applyLegacyVerdicts(store.nodes, verdicts);
      if (outcome.unchanged) return;
      store.setNodesTransient(outcome.nodes);
      applied = true;
    });
    return () => {
      cancelled = true;
      // Only when nothing landed. Clearing it unconditionally would re-resolve
      // after every successful migration too, and the rewritten cards no
      // longer look legacy, so `legacyCards(snapshot).length === 0` would stop
      // it anyway — but relying on that makes the latch mean two things.
      if (!applied && migratedForRef.current === canvasId) {
        migratedForRef.current = null;
      }
    };
  }, [loadStatus, canvasId, assetScopeId, nodeCount]);

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

  // Store viewport → React Flow transform. THE single owner of that
  // direction (canvas fluency Wave 1, Task 3 评审修复轮1).
  //
  // React Flow runs UNCONTROLLED — `CanvasSurface` seeds `defaultViewport`
  // once and React Flow owns the transform after that, which is what makes a
  // pan land on the same frame as the gesture. The price is that a viewport
  // the STORE writes moves nothing by itself. Four writes need bridging, and
  // the store marks all four with one `viewportEpoch` bump:
  //
  //   * the initial load (the row arrives AFTER the surface mounts, so the
  //     seed React Flow got was the identity viewport);
  //   * a canvas switch — `CanvasView` is NOT remounted when `:canvasId`
  //     changes, it re-renders with a new prop;
  //   * a realtime rebase (`applyRemoteUpdate`);
  //   * a conflict resolve (`resolveConflictWithServer`).
  //
  // Everything acted on is read LIVE from `getState()`. The subscribed
  // `viewportEpoch` is only the trigger. The first version of this effect read
  // a render-time `loadStatus` and got it wrong in the one case it was written
  // for: on a switch the loader effect flips the store to `'loading'`
  // synchronously, but an effect in the same commit still closes over
  // `'ready'` — React does not re-render between effects — so it latched the
  // NEW canvas id while applying the OLD canvas's viewport, and the re-run
  // after the new row landed returned early. Same-commit staleness is why the
  // `canvasId` check compares against the STORE's id (the `:275` idiom), not
  // against anything captured at render.
  //
  // The `onMoveEnd` this triggers reports the value just applied, which
  // `CanvasSurface`'s no-change guard drops — otherwise opening a canvas
  // would dirty it and schedule a save of the row just loaded.
  //
  // `rfReady` is in the deps for the not-yet-initialised case: a bump that
  // arrives before React Flow's `onInit` finds no instance, changes no
  // latch, and is applied by the re-run when the instance shows up.
  //
  // Declared BEFORE the heal effect below so that on a canvas whose saved
  // viewport frames nothing, the restore applies first and the heal's
  // `fitView` is what the user actually ends up looking at.
  const viewportEpoch = useCanvasCoreStore((s) => s.viewportEpoch);
  const viewportEpochAppliedRef = useRef(0);
  useEffect(() => {
    const instance = rfInstanceRef.current;
    if (!instance) return;
    const state = useCanvasCoreStore.getState();
    if (state.viewportEpoch === viewportEpochAppliedRef.current) return;
    if (state.canvasId !== canvasId) return;
    viewportEpochAppliedRef.current = state.viewportEpoch;
    void instance.setViewport(state.viewport);
  }, [viewportEpoch, canvasId, rfReady]);

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
  // own transform directly; the store learns the healed viewport through
  // `onMoveEnd` → `setViewportSettled` like any other settled move, so the
  // fit does get persisted — what it must never do is go through the store
  // FIRST and repaint from there.
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
    // `minZoom` floors the fit — see `VIEWPORT_HEAL_MIN_ZOOM` for the
    // arithmetic. React Flow clamps its computed fit zoom into
    // [minZoom, maxZoom] and centres on the content, so a stack too tall for
    // 0.7 simply overflows top and bottom instead of shrinking to a smear.
    instance.fitView({ padding: 0.2, minZoom: VIEWPORT_HEAL_MIN_ZOOM });
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

  // `?node=<id>` — the landing half of the Generated inbox's deep link
  // (`/team/{scope}/canvas/{id}?node=n9`). Unlike `focusShotId` this id is a
  // canvas node id straight out of `nodes_json`, so no `shot-` prefixing.
  //
  // Latched on `canvasId|nodeParam`, not on the param alone. Both halves of
  // that key are load-bearing:
  //  * the param, because navigating from one inbox card to another on the
  //    same canvas changes ONLY the query string, and that second click must
  //    move the viewport too;
  //  * the canvas id, because `CanvasView` is not remounted when the route's
  //    `:canvasId` changes — it re-renders with a new prop and the ref
  //    survives. Latching on the param alone would make canvas A → canvas B
  //    with the same node id a silent no-op.
  // What the latch is actually there to stop is a re-run at the SAME key:
  // StrictMode double-invokes mount effects, and without it the viewport
  // animates twice and selection is stamped twice on every open.
  const nodeParam = searchParams.get('node');
  const nodeParamHandledRef = useRef<string | null>(null);
  useEffect(() => {
    if (!nodeParam) return;
    if (loadStatus !== 'ready' || !reconcileDone) return;
    const instance = rfInstanceRef.current;
    if (!instance) return;
    const latchKey = `${canvasId}|${nodeParam}`;
    if (nodeParamHandledRef.current === latchKey) return;
    nodeParamHandledRef.current = latchKey;

    const exists = useCanvasCoreStore
      .getState()
      .nodes.some((n) => (n as Record<string, unknown>).id === nodeParam);
    if (!exists) {
      // A stale link (node deleted, wrong canvas) leaves the canvas exactly
      // as the user found it — same fire-and-forget contract as `focusShotId`.
      console.warn('[CanvasView] ?node= names no node on this canvas', {
        canvasId,
        nodeParam,
      });
      return;
    }
    useCanvasCoreStore.getState().setSelection([nodeParam]);
    instance.fitView({ nodes: [{ id: nodeParam }], duration: 400, padding: 0.35 });
  }, [nodeParam, loadStatus, reconcileDone, rfReady, canvasId]);

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
  //
  // `insetStyle` publishes how much of this surface the Library panel is
  // standing on. Every island that centres itself or anchors to the right
  // edge reads it from here; without it they lay themselves out against the
  // full width and slide underneath the open panel (see `libraryInset`).
  return (
    <div ref={surfaceRef} className="relative h-full w-full" style={insetStyle(inset)}>
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
      {/* Standard AND the four entity boards. The entity kinds used to be
          excluded because they seeded a preset workflow and needed no way to
          add anything; P4 deleted those templates, so a hand-made character /
          location / prop / costume board opened blank with NO way to add a
          node except the pane's drag-create menu — which a user has to know is
          there. `lite` stays out on its own terms: it deliberately offers a
          four-card menu instead of the full node set (see `DragCreateMenu`),
          and that is a product decision this change has no business
          reversing. */}
      {(kind === 'smart' || isEntityCanvas(kind)) && !readOnly && (
        <TopNodeBar surfaceRef={surfaceRef} />
      )}
      {/* No `!readOnly` gate, unlike its neighbours: `L` fires for a viewer, so
          gating this makes that key a silent no-op — see LibraryMediaPage. */}
      {isSmartFamily(kind) && <LibraryPanel />}
      {isSmartFamily(kind) && !readOnly && <ArrangeSelectedButton />}
      {isSmartFamily(kind) && !readOnly && (
        <CanvasComposer surfaceRef={surfaceRef} teamId={teamId} />
      )}
      <CanvasConflictDialog />
      <SaveBadge
        status={saveStatus}
        error={saveError}
        errorStatus={saveErrorStatus}
        readOnly={readOnly}
        t={t}
      />
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

/** Top-RIGHT, so it too has to stop where the Library panel starts. */
const BADGE_BASE =
  'absolute right-[calc(1rem_+_var(--canvas-inset-right,0px))] top-4 rounded-md px-3 py-1 text-xs font-medium shadow';
/** Neutral chrome (saving / read-only) — the canvas's own panel treatment. */
const BADGE_NEUTRAL = 'canvas-island text-canvas-text';
const BADGE_WARN = 'border border-warn-line bg-warn-soft text-warn';
const BADGE_DANGER = 'border border-danger-line bg-danger-soft text-danger';

function SaveBadge({
  status,
  error,
  errorStatus,
  readOnly,
  t,
}: {
  status: 'idle' | 'saving' | 'error';
  error: string | null;
  /** HTTP status behind `error`, or null when nothing answered — see the
   *  store's `saveErrorStatus`. */
  errorStatus: number | null;
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
        className={`pointer-events-none ${BADGE_BASE} ${BADGE_NEUTRAL}`}
        role="status"
        aria-live="polite"
      >
        {t('canvas.readOnly', 'Read-only')}
      </div>
    );
  }
  if (status === 'idle') return null;
  if (status === 'saving') {
    return (
      <div
        className={`pointer-events-none ${BADGE_BASE} ${BADGE_NEUTRAL}`}
        role="status"
        aria-live="polite"
      >
        {t('canvas.saveBadge.saving', 'Saving…')}
      </div>
    );
  }

  // A failure the user can act on (2026-08-13). The badge used to render a
  // flat "Save failed" while `saveError` — which already held the message —
  // was never read: the reported red badge turned out to be a request that
  // never reached the server at all (48h of production logs carry no failing
  // canvas PUT), and nothing on screen could have told the user or the person
  // debugging it that. Category comes from `classifySaveFailure`, which is a
  // pure function unit-tested on its own — no classification lives in here.
  const failure = classifySaveFailure(error, errorStatus);

  if (failure.kind === 'conflict') {
    return (
      <div
        className={`pointer-events-none ${BADGE_BASE} ${BADGE_WARN}`}
        role="status"
        aria-live="polite"
        title={t(
          'canvas.saveBadge.conflictTitle',
          'Someone else saved this canvas first. Resolve the conflict to continue saving.',
        )}
      >
        {t('canvas.saveBadge.conflict', 'Conflict')}
      </div>
    );
  }

  // Short category next to the label; the full message rides in `title`. Not
  // tooltip-ONLY: a reason nobody can see without hovering is barely better
  // than no reason, so the category is real text either way.
  // `HTTP <status>` is deliberately not translated — a status code is the
  // same token in every language and is what a bug report needs verbatim.
  const reason =
    failure.kind === 'server'
      ? `HTTP ${failure.status}`
      : failure.kind === 'unreachable'
        ? t('canvas.saveBadge.reasonUnreachable', 'no response')
        : null;
  const explanation =
    failure.kind === 'server'
      ? t('canvas.saveBadge.titleServer', 'The server refused this save.')
      : failure.kind === 'unreachable'
        ? t(
            'canvas.saveBadge.titleUnreachable',
            'The save request never reached the server — check your connection. Your edits are still here and will be retried on the next change.',
          )
        : t('canvas.saveBadge.titleUnknown', 'The save failed for an unknown reason.');
  const title = [explanation, failure.status !== null ? `HTTP ${failure.status}` : null, failure.message]
    .filter(Boolean)
    .join(' — ');

  return (
    // `pointer-events-auto` only on this branch: the native `title` tooltip
    // needs hover, and one small hoverable corner is a fair price for a
    // failure being readable. Every other badge state stays click-through so
    // it can never eat a pan gesture.
    <div
      className={`pointer-events-auto cursor-help ${BADGE_BASE} ${BADGE_DANGER}`}
      role="status"
      aria-live="polite"
      title={title}
    >
      <span>{t('canvas.saveBadge.failed', 'Save failed')}</span>
      {reason && <span className="opacity-80"> · {reason}</span>}
    </div>
  );
}
