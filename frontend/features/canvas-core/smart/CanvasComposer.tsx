/**
 * Smart-mode composer (Phase 2 of canvas + AI upgrade).
 *
 * Bottom toolbar:
 *   + Shot / + Prompt / + Output / + Loop
 *   Run                — execute selected prompts only
 *   Cascade Run        — execute every prompt in topo order
 *
 * Run uses the mock runner from `./runner` — the real provider adapter
 * (Jimeng CLI / nous-center) lands in the next slice; the lifecycle
 * UX is fully wired here so the swap is mechanical.
 *
 * New nodes drop at the visual centre of the surface (screenToWorld
 * via the viewport math).
 */

import { forwardRef, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router-dom';

import { arrangeSelected } from './arrangeNodes';
import { useKnifeStore } from '../../../canvas-kit/knifeStore';
import { prepareDuplicate } from '../store/clipboard';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { screenToWorld } from '../utils/viewport';
import {
  createLoopNode,
  createOutputNode,
  createLlmNode,
  createPromptNode,
  createShotNode,
  createTimelineNode,
} from './factories';
import { groupSelection, ungroupNode } from './grouping';
import {
  cancelPendingGenTasks,
  clearPendingGenTasks,
  prunePendingGenTask,
} from './genResume';
import { onGenerationDispatched } from './dispatchEffects';
import { resolveAssetInputsForRun } from './assetInputs';
import { markDroppedKnobs } from './droppedKnobs';
import {
  appendGenerationResults,
  markGenerationRecover,
  settleGenerationSlot,
} from './genSlots';
import { resolveAssetRef } from './assetRef';
import { promptBodyForRun } from './mentionedAssets';
import { buildPromptReferenceMedia } from './promptReferenceMedia';
import { importResourceAsCanvasMedia } from './mediaImport';
import {
  resolveEffectiveSourceUrl,
  resolveEffectiveSourceUrls,
} from './promptInputs';
import { rerunPrompt } from './regenerate';
import { withGenerationRunner } from './generationRunner';
import { createBackendRunner } from './runner.backend';
import {
  mockRunner,
  runPrompts,
  type PromptCaller,
  type RunHandlers,
  type RunnerContext,
} from './runner';
import { topoSortPrompts } from './topology';
import type { CanvasConnection, CanvasNode } from '../types';
import type { PromptNodeData } from './types';
import { getResourceCoverUrl } from '../../../services/resourceService';
import { SMART_NODE_TYPES } from './nodes/registry';
import { parseWorkflow, serializeWorkflow, workflowFilename } from './workflowIO';
import { fetchWorkflowText, saveWorkflowToLibrary } from './workflowLibrary';
import { WorkflowLibraryPicker } from './WorkflowLibraryPicker';

/** Shape of the router state SendToCanvasModal navigates here with
 *  (spec 2026-07-26-asset-prompt-management, Phase 2 Task 4 / Phase 3 Task
 *  3; `autoRun`/`ratio` added by spec 2026-07-28-prompt-dataline, Task 5).
 *  The insert mints a fresh durable URL from `assetId` via
 *  importResourceAsCanvasMedia (falling back to `coverUrl`, else the
 *  resource's cover, only if that mint fails), then hands it to
 *  buildPromptReferenceMedia, which owns the media-node + connection
 *  construction. When `autoRun` is set, the
 *  inserted prompt node gets `gen: {kind:'image', model:'', ratio}` merged
 *  in before the commit, then `rerunPrompt(id)` fires once — this is the
 *  "⚡ Generate Similar" one-click flow from a resource's result card. */
interface PendingPromptInsert {
  /** The resource behind the prompt. ABSENT for a text-only insert (ruling
   *  R11): a prompt template has no resource row, so there is nothing to
   *  import and no cover to fall back to — the canvas gets the prompt node
   *  alone. Present means a real `resources.id`, never an asset id. */
  assetId?: string;
  filename: string;
  positive: string;
  negative?: string;
  coverUrl?: string;
  /** Generate Similar (Task 5): auto-run the inserted prompt node right
   *  after it lands, seeded with the source asset's aspect ratio. */
  autoRun?: boolean;
  ratio?: string;
}

/** M4: `insert.ratio` comes from the LLM-produced `aspect_ratio` field in
 *  gen_prompt_json (via PromptSection's "Generate Similar") — an
 *  unvalidated free-form string. A malformed value merged straight into
 *  `gen.ratio` reaches the generation backend and 422s the autoRun call
 *  silently; validate the shape here and omit the field instead. */
const VALID_RATIO_RE = /^\d{1,2}:\d{1,2}$/;

const SMART_NODE_TYPE_KEYS = new Set(Object.keys(SMART_NODE_TYPES));
/** Refuse absurd files before reading them into memory. */
const MAX_WORKFLOW_FILE_BYTES = 5 * 1024 * 1024;

interface CanvasComposerOptions {
  /** When passed, new nodes are positioned at the centre of this DOM
   *  rect (translated into world coords). Falls back to viewport
   *  origin when not provided — useful for tests / headless renders. */
  surfaceRef?: React.RefObject<HTMLElement | null>;
  /** Override the runner — tests pass a deterministic one. Default at
   *  runtime is the backend runner bound to the open canvas; if no
   *  canvas is loaded yet, falls back to the mock so the UX is still
   *  exercisable in isolation. */
  runner?: PromptCaller;
  /** Team scope for the workflow library glue (②-4) — save/import needs
   *  the resource-library scope id. Library buttons hide without it. */
  teamId?: string;
}

export function CanvasComposer({
  surfaceRef,
  runner: runnerOverride,
  teamId,
}: CanvasComposerOptions = {}) {
  const { t } = useTranslation();
  // Settled viewport — see the note in `TopNodeBar`: new nodes are placed by
  // a discrete click at rest, and this component renders outside React Flow's
  // provider, so the store is both correct and the only option.
  const viewport = useCanvasCoreStore((s) => s.viewport);
  // Lite ("Smart") canvases hide the workflow-only add buttons — the IC
  // four-card create menu is their primary surface.
  const canvasKind = useCanvasCoreStore((s) => s.kind);
  const isLite = canvasKind === 'lite';
  const loadStatus = useCanvasCoreStore((s) => s.loadStatus);
  const nodes = useCanvasCoreStore((s) => s.nodes);
  const connections = useCanvasCoreStore((s) => s.connections);
  const selection = useCanvasCoreStore((s) => s.selection);
  const canvasId = useCanvasCoreStore((s) => s.canvasId);
  const setNodes = useCanvasCoreStore((s) => s.setNodes);
  const setConnections = useCanvasCoreStore((s) => s.setConnections);
  const setSelection = useCanvasCoreStore((s) => s.setSelection);
  const patchNode = useCanvasCoreStore((s) => s.patchNode);
  const location = useLocation();
  const navigate = useNavigate();

  // Per-node run batches (P2-9 — Infinite's node-granular running state):
  // each Run/Cascade owns an independent batch with its own cooperative
  // stop flag, so a long video generation no longer freezes the composer.
  // The map lives in a ref (stable identity for the memoized runner);
  // counters mirror it into state for the button chrome.
  const batchesRef = useRef(
    new Map<number, { ids: Set<string>; stop: { requested: boolean } }>(),
  );
  const nextBatchIdRef = useRef(0);
  const [activeBatchCount, setActiveBatchCount] = useState(0);
  const [cascadeCount, setCascadeCount] = useState(0);
  const [stopRequested, setStopRequested] = useState(false);
  const knifeActive = useKnifeStore((s) => s.active);
  const toggleKnife = useKnifeStore((s) => s.toggle);

  /** Is this prompt owned by any in-flight batch? */
  const inAnyBatch = useCallback((promptId: string): boolean => {
    for (const batch of batchesRef.current.values()) {
      if (batch.ids.has(promptId)) return true;
    }
    return false;
  }, []);

  const runner = useMemo<PromptCaller>(() => {
    const base = runnerOverride
      ? runnerOverride
      : canvasId
        ? createBackendRunner({ canvasId })
        : mockRunner;
    // Image/video prompts route through the G4-B1 generation tasks; text
    // prompts pass straight through to the base caller. onPhase surfaces
    // the engine queue (jimeng) as a 'queued' halo mid-poll (G4-F3).
    return withGenerationRunner(base, {
      canvasId,
      onPhase: (id, phase) => {
        useCanvasCoreStore.getState().patchNode(id, {
          data: { run_status: phase === 'queued' ? 'queued' : 'running' },
        });
      },
      // Per-prompt stop resolution (P2-9): a poll aborts only when ITS
      // batch was stopped, not when any run anywhere was.
      shouldStop: (promptId) => {
        for (const batch of batchesRef.current.values()) {
          if (batch.ids.has(promptId)) return batch.stop.requested;
        }
        return false;
      },
      // Placeholder lifecycle (P0-3): shimmer cells appear at dispatch,
      // each finished item replaces one (first-done-first-shown), failures
      // burn a cell into the failed count. Task ids persist on the prompt
      // node (P1-13) so a reload resumes the batch; a broken poll becomes
      // a recover mark ("task not lost") instead of a silent failure.
      onDispatched: onGenerationDispatched,
      // What the provider could not honour lands on the node beside the run
      // badge — the post-run half of the footer's stranded-value mark.
      // Asset cards wired upstream contribute references, prompt text and a
      // negative — resolved per run because the answer depends on the model.
      assetInputs: resolveAssetInputsForRun,
      onDropped: markDroppedKnobs,
      onItemSettled: (id, item) => {
        if (item.url) appendGenerationResults(id, [item.url], item.kind);
        else if (item.recoverable) markGenerationRecover(id, item.taskId, item.kind);
        else settleGenerationSlot(id, { failed: 1 });
        prunePendingGenTask(id, item.taskId);
      },
    });
  }, [runnerOverride, canvasId]);

  const dropPosition = useCallback((): { x: number; y: number } => {
    const rect = surfaceRef?.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    const screenCenter = {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
    return screenToWorld(screenCenter, viewport);
  }, [surfaceRef, viewport]);

  const addNode = useCallback(
    (kind: 'shot' | 'prompt' | 'llm' | 'output' | 'loop' | 'timeline') => {
      const position = dropPosition();
      const node =
        kind === 'shot'
          ? createShotNode({}, { position })
          : kind === 'llm'
            ? createLlmNode({}, { position })
          : kind === 'prompt'
            ? createPromptNode({}, { position })
            : kind === 'output'
              ? createOutputNode({}, { position })
              : kind === 'loop'
                ? createLoopNode({}, { position })
                : createTimelineNode({}, { position });
      setNodes([...nodes, node]);
      setSelection([node.id]);
    },
    [dropPosition, nodes, setNodes, setSelection],
  );

  // Send-to-Canvas consumption (Phase 2 Task 4): SendToCanvasModal
  // navigates here with `location.state.promptInsert`. Once the canvas
  // has actually finished loading (nodes/connections are the real
  // document, not the reset-store defaults), turn the payload into a
  // fresh Prompt node + Media node pair via the same pure builder the
  // in-canvas Library picker uses, then strip the state so a reload or
  // back-nav doesn't reinsert it.
  // insertedRef guards against StrictMode's synchronous double-invoke of
  // this effect (mirrors CanvasPage's seededRef pattern) — the navigate()
  // state-clear alone doesn't help because both invocations run before
  // either commit lands, so both would still see the same pending insert.
  const insertedRef = useRef<string | null>(null);
  useEffect(() => {
    if (loadStatus !== 'ready') return;
    const insert = (location.state as { promptInsert?: PendingPromptInsert } | null)
      ?.promptInsert;
    if (!insert) return;
    // A text-only insert has no id to key on, so the key is what does vary
    // between two of them. Two sends of the same text to the same canvas
    // within one mount still collapse to one — the guard cannot tell them
    // apart, and inserting the same prompt twice is the worse failure.
    const insertKey = insert.assetId
      ? `${canvasId}:${insert.assetId}`
      : `${canvasId}:${insert.filename}:${insert.positive.length}`;
    // Guard is check-and-set BEFORE the mint's await below so StrictMode's
    // synchronous double-invoke can't both pass the check and each mint/
    // insert their own pair — both invocations run before either commit
    // lands, so the guard has to win the race at the top, not after.
    if (insertedRef.current === insertKey) return;
    insertedRef.current = insertKey;

    void (async () => {
      const position = dropPosition();
      const promptNode = createPromptNode({}, { position });

      // The payload already carries the resolved positive/negative text
      // (the sender picked the lang side), so the patch is the same
      // expression on both branches. Each key is omitted when empty: an
      // empty `body` would blow away text the user had already typed into
      // the node, and an empty `negative_body` would render the negative
      // textarea for no reason. promptPatch is spread over the node's
      // existing data at the commit below, so an omitted key is a no-op.
      const promptPatch: { body?: string; negative_body?: string } = {
        ...(insert.positive.trim() ? { body: insert.positive } : {}),
        ...(insert.negative ? { negative_body: insert.negative } : {}),
      };

      // Text-only insert: no import, no media node, no connection. Minting a
      // cover from an id the payload does not have would put a tile that
      // resolves to nothing next to the prompt.
      let media: ReturnType<typeof buildPromptReferenceMedia> | null = null;

      if (insert.assetId) {
        let mediaUrl: string;
        let mediaKind: 'image' | 'video';
        try {
          const imported = await importResourceAsCanvasMedia(insert.assetId);
          mediaUrl = imported.url;
          mediaKind = imported.kind;
        } catch (err) {
          console.error('[promptAsset] durable import failed, falling back to cover:', err);
          // The sender's own picture when it gave one (an album slide is not
          // the album's cover), else the resource's cover.
          mediaUrl = insert.coverUrl ?? getResourceCoverUrl(insert.assetId); // visual-only fallback, no i2i
          mediaKind = 'image'; // cover endpoint always serves an image
        }

        media = buildPromptReferenceMedia({
          promptNodeId: promptNode.id,
          promptNodePosition: position,
          mediaUrl,
          mediaKind,
          name: insert.filename,
        });
      }
      const filledPromptNode = {
        ...promptNode,
        data: {
          ...promptNode.data,
          ...promptPatch,
          // Generate Similar (Task 5): seed gen settings BEFORE the commit
          // below so rerunPrompt (which reads node data from the store at
          // call time) sees them on its very first read.
          ...(insert.autoRun
            ? {
                gen: {
                  kind: 'image' as const,
                  model: '',
                  ratio: insert.ratio && VALID_RATIO_RE.test(insert.ratio) ? insert.ratio : undefined,
                },
              }
            : {}),
        },
      };

      const store = useCanvasCoreStore.getState();
      if (media) {
        setNodes([...store.nodes, filledPromptNode, media.mediaNode as unknown as CanvasNode]);
        setConnections([...store.connections, media.connection as unknown as CanvasConnection]);
        setSelection([filledPromptNode.id, media.mediaNode.id]);
      } else {
        setNodes([...store.nodes, filledPromptNode]);
        setSelection([filledPromptNode.id]);
      }

      if (insert.autoRun) {
        rerunPrompt(filledPromptNode.id).catch((err) =>
          console.error('[promptAsset] autoRun rerunPrompt failed:', err),
        );
      }

      navigate(location.pathname + location.search + location.hash, { replace: true });
    })();
  }, [
    loadStatus,
    location.state,
    location.pathname,
    location.search,
    location.hash,
    navigate,
    dropPosition,
    setNodes,
    setConnections,
    setSelection,
    canvasId,
  ]);

  const buildContexts = useCallback(
    (ids: string[]): RunnerContext[] => {
      const byId = new Map(
        nodes.map((n) => [(n as Record<string, unknown>).id as string, n]),
      );
      return ids
        .map((id): RunnerContext | null => {
          const n = byId.get(id);
          if (!n) return null;
          const data = (n as Record<string, unknown>).data as
            | PromptNodeData
            | undefined;
          if (!data) return null;
          return {
            promptId: id,
            body: promptBodyForRun(data),
            provider_slug: data.provider_slug,
            agent_id: data.agent_id,
            gen: data.gen ?? null,
            source_url: resolveEffectiveSourceUrl(n as never, nodes, connections),
            source_urls: resolveEffectiveSourceUrls(n as never, nodes, connections),
            negative_body: data.negative_body ?? null,
            asset_ref: resolveAssetRef(id, nodes, connections),
          };
        })
        .filter((v): v is RunnerContext => v !== null);
    },
    [nodes, connections],
  );

  const handlers: RunHandlers = {
    onStatusChange: (id, status, fields) => {
      patchNode(id, { data: { run_status: status, ...fields } });
    },
    onResult: (id, result) => {
      // Generation results now land progressively via onItemSettled (P0-3)
      // — re-upserting here would archive the batch we just filled. This
      // hook only sweeps leftovers: a stopped/failed run clears remaining
      // shimmer cells; text results have no slot at all.
      if (result.media_kind && (!result.ok || result.stopped)) {
        settleGenerationSlot(id, { clearPending: true });
      }
      // The run settled in THIS session — nothing left to resume (P1-13).
      if (result.media_kind) clearPendingGenTasks(id);
    },
  };

  const doRunIds = useCallback(
    async (ids: string[], opts?: { cascade?: boolean }) => {
      // Filter out prompts that are already owned by an in-flight batch OR
      // marked in flight in the store (loop/rerun channels) — the double
      // dispatch would race the SAME gen_slot.
      const liveNodes = useCanvasCoreStore.getState().nodes;
      const inFlightStatus = new Set(['queued', 'running']);
      const eligible = ids.filter((id) => {
        if (inAnyBatch(id)) return false;
        const node = liveNodes.find(
          (n) => (n as Record<string, unknown>).id === id,
        );
        const status = (
          (node as Record<string, unknown> | undefined)?.data as
            | { run_status?: string }
            | undefined
        )?.run_status;
        return !inFlightStatus.has(status ?? '');
      });
      if (eligible.length === 0) return;

      const batch = { ids: new Set(eligible), stop: { requested: false } };
      const key = nextBatchIdRef.current++;
      batchesRef.current.set(key, batch);
      setActiveBatchCount((c) => c + 1);
      if (opts?.cascade) setCascadeCount((c) => c + 1);
      try {
        await runPrompts(buildContexts(eligible), runner, handlers, {
          shouldStop: () => batch.stop.requested,
        });
      } finally {
        batchesRef.current.delete(key);
        setActiveBatchCount((c) => c - 1);
        if (opts?.cascade) setCascadeCount((c) => c - 1);
        // Last batch drained → the Stopping… label has nothing left to stop.
        if (batchesRef.current.size === 0) setStopRequested(false);
      }
    },
    [buildContexts, handlers, runner, inAnyBatch],
  );

  const onStopRun = useCallback(() => {
    // Stop EVERY active batch (per-node stop is a follow-up).
    for (const batch of batchesRef.current.values()) {
      batch.stop.requested = true;
      // Really cancel the in-flight backend generation tasks (P1-1) — Stop
      // used to only abandon the poll while the DBOS task kept billing.
      // Fire-and-forget; cancel is idempotent and best-effort.
      for (const promptId of batch.ids) {
        void cancelPendingGenTasks(promptId);
      }
    }
    setStopRequested(true);
  }, []);

  const onRunSelected = useCallback(() => {
    const promptIds = nodes
      .filter((n) => (n as Record<string, unknown>).type === 'prompt')
      .map((n) => (n as Record<string, unknown>).id as string)
      .filter((id) => selection.includes(id));
    void doRunIds(promptIds);
  }, [nodes, selection, doRunIds]);

  const onCascadeRun = useCallback(() => {
    const { order } = topoSortPrompts(nodes, connections);
    void doRunIds(order, { cascade: true });
  }, [nodes, connections, doRunIds]);

  const onArrange = useCallback(() => {
    // Arrange-selected (IC's 整理选中): setNodes (not patchNode) so one
    // layout pass = one undoable edit. Shared with the floating button.
    const next = arrangeSelected(nodes, connections, selection);
    if (next) setNodes(next);
  }, [nodes, connections, selection, setNodes]);

  const selectedGroupId = useMemo(() => {
    const sel = new Set(selection);
    const g = nodes.find(
      (n) =>
        sel.has((n as Record<string, unknown>).id as string) &&
        (n as Record<string, unknown>).type === 'group',
    );
    return g ? String((g as Record<string, unknown>).id) : null;
  }, [nodes, selection]);

  const onGroup = useCallback(() => {
    const result = groupSelection(nodes, selection);
    if (!result) return;
    setNodes(result.nodes);
    setSelection([result.groupId]);
  }, [nodes, selection, setNodes, setSelection]);

  const onUngroup = useCallback(() => {
    if (!selectedGroupId) return;
    setNodes(ungroupNode(nodes, selectedGroupId));
    setSelection([]);
  }, [nodes, selectedGroupId, setNodes, setSelection]);

  // ---- Workflow export / import (G5) ------------------------------------
  const importInputRef = useRef<HTMLInputElement | null>(null);
  const [workflowError, setWorkflowError] = useState<string | null>(null);

  const onExport = useCallback(() => {
    const selectedSet = new Set(selection);
    const selected = nodes.filter((n) =>
      selectedSet.has((n as Record<string, unknown>).id as string),
    );
    if (selected.length === 0) return;
    const payload = serializeWorkflow('smart', selected, connections);
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: 'application/json',
    });
    const href = URL.createObjectURL(blob);
    try {
      const link = document.createElement('a');
      link.href = href;
      link.download = workflowFilename(payload.nodes.length);
      document.body.appendChild(link);
      link.click();
      link.remove();
    } finally {
      URL.revokeObjectURL(href);
    }
  }, [nodes, connections, selection]);

  const importWorkflowText = useCallback((text: string) => {
    setWorkflowError(null);
    try {
      const store = useCanvasCoreStore.getState();
      const payload = parseWorkflow(text, {
        allowedTypes: SMART_NODE_TYPE_KEYS,
        expectedKind: store.kind,
      });
      const existing = new Set(
        store.nodes.map((n) => (n as Record<string, unknown>).id as string),
      );
      const prepared = prepareDuplicate(payload.nodes, payload.connections, existing);
      if (!prepared) return;
      store.setNodes([...store.nodes, ...prepared.nodes]);
      if (prepared.connections.length > 0) {
        store.setConnections([...store.connections, ...prepared.connections]);
      }
      store.setSelection(
        prepared.nodes.map((n) => (n as Record<string, unknown>).id as string),
      );
    } catch (err) {
      setWorkflowError(err instanceof Error ? err.message : 'Failed to import workflow');
    }
  }, []);

  const onImportFile = useCallback(
    async (file: File) => {
      setWorkflowError(null);
      if (file.size > MAX_WORKFLOW_FILE_BYTES) {
        setWorkflowError('Workflow file is too large (5 MB limit)');
        return;
      }
      importWorkflowText(await file.text());
    },
    [importWorkflowText],
  );

  // ---- Library glue (②-4): save into / import from the resource library.
  const [savingToLibrary, setSavingToLibrary] = useState(false);
  const [libraryNotice, setLibraryNotice] = useState<string | null>(null);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const libraryButtonRef = useRef<HTMLButtonElement | null>(null);

  // Auto-dismiss the save notice: it renders at the same bottom-full anchor
  // as the library picker, so a persistent notice covers the picker panel
  // (open Library right after Save and the green bar sits on the list).
  useEffect(() => {
    if (!libraryNotice) return undefined;
    const timer = setTimeout(() => setLibraryNotice(null), 2500);
    return () => clearTimeout(timer);
  }, [libraryNotice]);

  const onSaveToLibrary = useCallback(async () => {
    if (!teamId || savingToLibrary) return;
    const selectedSet = new Set(selection);
    const selected = nodes.filter((n) =>
      selectedSet.has((n as Record<string, unknown>).id as string),
    );
    if (selected.length === 0) return;
    setSavingToLibrary(true);
    setWorkflowError(null);
    setLibraryNotice(null);
    try {
      const payload = serializeWorkflow('smart', selected, connections);
      const resource = await saveWorkflowToLibrary(payload, teamId);
      setLibraryNotice(`Saved as workflow: ${String((resource as { filename?: string }).filename ?? 'workflow')}`);
    } catch (err) {
      console.error('[CanvasComposer] save workflow to library failed:', err);
      setWorkflowError('Could not save this workflow');
    } finally {
      setSavingToLibrary(false);
    }
  }, [teamId, savingToLibrary, selection, nodes, connections]);

  const onImportFromLibrary = useCallback(
    async (resourceId: string) => {
      setWorkflowError(null);
      setLibraryNotice(null);
      try {
        importWorkflowText(await fetchWorkflowText(resourceId));
      } catch (err) {
        console.error('[CanvasComposer] library import failed:', err);
        setWorkflowError(
          err instanceof Error ? err.message : 'Failed to import from the library',
        );
      }
    },
    [importWorkflowText],
  );

  return (
    <div
      role="toolbar"
      aria-label="Smart canvas composer"
      // max-w + x-scroll: the island must never push its tail buttons off
      // screen (the Stop key made the long-standing narrow-viewport
      // overflow visible at 1280px).
      // `left-0` + `right-[inset]` + `mx-auto`, NOT `inset-x-0`: the island
      // centres in what the Library panel leaves, so opening the panel
      // slides it out from under instead of hiding it. `max-w` is a
      // percentage of the SURFACE, not of that band, so it has to subtract
      // the reservation a second time. The bottom var is the drawer layout
      // below 1100px, where the panel takes the bottom edge instead.
      className="canvas-island pointer-events-auto absolute left-0 right-[var(--canvas-inset-right,0px)] bottom-[calc(1rem_+_var(--canvas-inset-bottom,0px))] mx-auto flex w-fit max-w-[calc(100%_-_var(--canvas-inset-right,0px)_-_2rem)] gap-1 overflow-x-auto p-1.5"
    >
      {/* Standard canvases add nodes from the TOP node bar (Phase 2.2) —
          duplicating them here made two menu rows. Only the lite canvas,
          which has no top bar, keeps its two quick-add buttons. */}
      {isLite && (
        <>
          <ComposerButton onClick={() => addNode('prompt')}>+ Prompt</ComposerButton>
          <ComposerButton onClick={() => addNode('loop')}>+ Loop</ComposerButton>
          <Divider />
        </>
      )}
      <ComposerButton onClick={onArrange} disabled={nodes.length === 0}>
        Arrange
      </ComposerButton>
      <ComposerButton onClick={toggleKnife} emphasis={knifeActive ? 'primary' : undefined}>
        {knifeActive ? 'Knife ✕' : 'Knife'}
      </ComposerButton>
      {selectedGroupId ? (
        <ComposerButton onClick={onUngroup}>Ungroup</ComposerButton>
      ) : (
        <ComposerButton onClick={onGroup} disabled={selection.length < 2}>
          Group
        </ComposerButton>
      )}
      <ComposerButton onClick={onExport} disabled={selection.length === 0}>
        Export
      </ComposerButton>
      <ComposerButton onClick={() => importInputRef.current?.click()}>
        Import
      </ComposerButton>
      {teamId && (
        <>
          <ComposerButton
            onClick={() => void onSaveToLibrary()}
            disabled={selection.length === 0 || savingToLibrary}
          >
            {savingToLibrary ? 'Saving…' : 'Save'}
          </ComposerButton>
          <ComposerButton ref={libraryButtonRef} onClick={() => setLibraryOpen((v) => !v)}>
            {t('canvas.library.workflows', 'Workflows')}
          </ComposerButton>
        </>
      )}
      {libraryOpen && teamId && (
        <WorkflowLibraryPicker
          anchorEl={libraryButtonRef.current}
          teamId={teamId}
          onPick={(id) => {
            setLibraryOpen(false);
            void onImportFromLibrary(id);
          }}
          onClose={() => setLibraryOpen(false)}
        />
      )}
      {libraryNotice && (
        <div
          role="status"
          className="absolute bottom-full left-1/2 mb-2 -translate-x-1/2 whitespace-nowrap rounded bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white shadow-lg"
        >
          {libraryNotice}
        </div>
      )}
      <input
        ref={importInputRef}
        data-testid="workflow-import-input"
        type="file"
        accept="application/json,.json"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = ''; // allow re-importing the same file
          if (file) void onImportFile(file);
        }}
      />
      {workflowError && (
        <div
          role="alert"
          className="absolute bottom-full left-1/2 mb-2 -translate-x-1/2 whitespace-nowrap rounded bg-rose-600 px-3 py-1.5 text-xs font-medium text-white shadow-lg"
        >
          {workflowError}
        </div>
      )}
      <Divider />
      {/* Per-node batches (P2-9): Run/Cascade stay live while batches run —
          Stop appears BESIDE them and stops every active batch (P0-4
          semantics per batch: unstarted prompts never dispatch, in-flight
          polls abandon, nodes return to idle; the backend tasks themselves
          keep running — no cancel endpoint yet). */}
      {activeBatchCount > 0 && (
        <ComposerButton onClick={onStopRun} disabled={stopRequested}>
          {stopRequested ? 'Stopping…' : 'Stop'}
        </ComposerButton>
      )}
      <ComposerButton
        onClick={onRunSelected}
        disabled={selection.length === 0}
      >
        Run
      </ComposerButton>
      <ComposerButton
        onClick={onCascadeRun}
        // A second cascade while one runs would race the same topo order.
        disabled={cascadeCount > 0}
        emphasis="primary"
      >
        Cascade Run
      </ComposerButton>
    </div>
  );
}

function Divider() {
  return <div className="mx-1 my-1 w-px bg-canvas-line" />;
}

const ComposerButton = forwardRef<
  HTMLButtonElement,
  {
    onClick: () => void;
    children: React.ReactNode;
    disabled?: boolean;
    emphasis?: 'primary';
  }
>(function ComposerButton({ onClick, children, disabled, emphasis }, ref) {
  // Infinite's .tool-btn (P1-7): bordered pill, 38px tall, 11px/700 label —
  // the borderless 14px/500 text buttons read as a different product next
  // to it (the visual diff's top structural gap).
  const primary =
    'border-transparent bg-canvas-strong text-canvas-card hover:opacity-90 disabled:opacity-40';
  const neutral =
    'border-canvas-line bg-canvas-card/60 text-canvas-text hover:bg-canvas-card disabled:text-canvas-muted/50';
  return (
    <button
      ref={ref}
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex h-[38px] min-w-[38px] items-center justify-center whitespace-nowrap rounded-full border px-3 text-[11px] font-bold ${
        emphasis === 'primary' ? primary : neutral
      } disabled:cursor-not-allowed`}
    >
      {children}
    </button>
  );
});
