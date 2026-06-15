/**
 * ClassicMode run bar — Phase 5a (synchronous cascade) + Phase 6d-M4b (async DBOS).
 *
 * TWO run paths, selected by which prop is provided:
 *
 * ── Synchronous cascade (legacy / test injection) ──────────────────────────
 * When a `cascade` prop is supplied the bar behaves exactly as it did before
 * Phase 6d: it calls `runClassicCascade` in-browser, receives `onNodePatch`
 * callbacks, and surfaces toasts for failures. All existing ClassicRunBar
 * tests exercise this path.
 *
 * ── Async DBOS graph-run (production default) ──────────────────────────────
 * When NO `cascade` prop is supplied (production), the button triggers:
 *   1. `topoSort` over the live store graph → deterministic `node_order`.
 *   2. Pre-paint every ordered node as `queued` for instant visual feedback.
 *   3. `POST /api/v1/canvases/{id}/graph-runs` (returns task_id immediately).
 *   4. Store `parentTaskId`; a `useEffect` watches `tasks` from
 *      `TaskManagerContext` and maps per-node subtask rows
 *      (`{task_id}-node-{pos}`, `metadata.node_id`) to node `run_status`.
 *
 * Node OUTPUT updates arrive via the Phase-6a Supabase Realtime channel
 * (`useCanvasRealtime` → `applyRemoteUpdate`) — this bar does NOT re-fetch.
 *
 * ── Single-node Run (SmartMode) ────────────────────────────────────────────
 * The SmartMode single-node `runSinglePrompt` path is UNAFFECTED — it lives
 * in `smart/runner.ts` and is invoked from SmartMode prompt node controls,
 * not from this component.
 */

import { useCallback, useEffect, useState } from 'react';
import { Loader2, Play } from 'lucide-react';

import { useToast } from '../../../../components/Toast';
import {
  useTaskManager,
  type UnifiedTask,
} from '../../../../contexts/TaskManagerContext';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { enqueueGraphRun } from '../../services/canvasService';
import { topoSort } from '../../smart/topology';
import { runClassicCascade } from '../cascade';
import { createClassicBackendRunner } from '../classicRunner';

// ── Types ──────────────────────────────────────────────────────────────────

/** Valid `run_status` values (mirrors `ClassicRunStatus` / `PromptNodeData`). */
type RunStatus = 'idle' | 'queued' | 'running' | 'succeeded' | 'failed' | 'blocked';

export interface ClassicRunBarProps {
  /**
   * Injected by existing tests with a deterministic spy (synchronous cascade
   * path). When present the async DBOS path is bypassed entirely so the full
   * legacy test suite continues to pass without any changes.
   *
   * Production callers MUST NOT supply this prop — leave it undefined so the
   * DBOS graph-run path is used.
   */
  cascade?: typeof runClassicCascade;

  /**
   * Injectable override for the graph-run enqueue call. Defaults to the real
   * `enqueueGraphRun` from canvasService. Provided by new DBOS-path tests.
   */
  enqueue?: typeof enqueueGraphRun;
}

// ── Helpers ────────────────────────────────────────────────────────────────

/** Map a `task_tracking.phase` value to the canvas node `run_status` union. */
function phaseToRunStatus(phase: string | undefined): RunStatus {
  switch (phase) {
    case 'queued':
    case 'dedup_check':
      return 'queued';
    case 'processing':
      return 'running';
    case 'completed':
      return 'succeeded';
    case 'failed':
    case 'cancelled':
      return 'failed';
    default:
      // Unknown / undefined phase while the workflow is live → treat as running.
      return 'running';
  }
}

/** True when every subtask has reached a terminal phase. */
function allTerminal(subtasks: UnifiedTask[]): boolean {
  if (subtasks.length === 0) return false;
  return subtasks.every(
    (t) => t.phase === 'completed' || t.phase === 'failed' || t.phase === 'cancelled',
  );
}

// ── Component ──────────────────────────────────────────────────────────────

export function ClassicRunBar({
  cascade,
  enqueue = enqueueGraphRun,
}: ClassicRunBarProps = {}) {
  const nodes = useCanvasCoreStore((s) => s.nodes);
  const connections = useCanvasCoreStore((s) => s.connections);
  const canvasId = useCanvasCoreStore((s) => s.canvasId);
  const patchNode = useCanvasCoreStore((s) => s.patchNode);
  const { addToast } = useToast();

  // TaskManagerContext — needed for the async DBOS path.
  // Tests mock this module (see ClassicRunBar.test.tsx / ClassicRunBar.graphRun.test.tsx).
  const { tasks } = useTaskManager();

  const [running, setRunning] = useState(false);
  /** DBOS parent workflow id after a successful graph-run enqueue. */
  const [parentTaskId, setParentTaskId] = useState<string | null>(null);

  const nodeCount = nodes.length;

  // ── Per-node status sync (DBOS path only) ────────────────────────────────
  // Subtasks are task_tracking rows with ids `{parentTaskId}-node-{position}`
  // and `metadata.node_id` = the canvas node's id. When their `phase` changes
  // (via Supabase Realtime → TaskManagerContext), we paint the matching node.
  useEffect(() => {
    if (!parentTaskId) return;
    const prefix = `${parentTaskId}-node-`;
    const subtasks = tasks.filter((t: UnifiedTask) => t.id.startsWith(prefix));
    for (const subtask of subtasks) {
      const nodeId = subtask.metadata.node_id as string | undefined;
      if (!nodeId) continue;
      const runStatus = phaseToRunStatus(subtask.phase);
      patchNode(nodeId, {
        data: { run_status: runStatus } as Record<string, unknown>,
      });
    }
    if (allTerminal(subtasks)) {
      setRunning(false);
      setParentTaskId(null);
    }
  }, [tasks, parentTaskId, patchNode]);

  // ── Run handler ──────────────────────────────────────────────────────────
  const onRun = useCallback(async () => {
    if (running || !canvasId) return;
    setRunning(true);

    // ── Legacy synchronous cascade (injected by tests) ──────────────────
    if (cascade) {
      try {
        const runner = createClassicBackendRunner({ canvasId });
        await cascade(nodes, connections, runner, {
          onNodePatch: (id, patch) =>
            patchNode(id, { data: { ...patch } as Record<string, unknown> }),
          onToast: (message) => addToast(message, 'error'),
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        addToast(`Cascade failed: ${message}`, 'error');
      } finally {
        setRunning(false);
      }
      return;
    }

    // ── Async DBOS graph-run (production default) ────────────────────────
    try {
      const nodeIds = nodes
        .map((n) => (n as Record<string, unknown>).id as string)
        .filter(Boolean);
      const edges = connections.map((c) => {
        const conn = c as Record<string, unknown>;
        return {
          source: conn.source as string,
          target: conn.target as string,
        };
      });
      const { order } = topoSort(nodeIds, edges);

      // Optimistic pre-paint: mark each node queued before the round-trip.
      for (const id of order) {
        patchNode(id, { data: { run_status: 'queued' } as Record<string, unknown> });
      }

      const result = await enqueue(canvasId, order, /* continueOnFailure */ true);
      setParentTaskId(result.task_id);
      // `running` stays true — cleared by the useEffect when subtasks finish.
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      addToast(`Graph run failed: ${message}`, 'error');
      setRunning(false);
      setParentTaskId(null);
    }
  }, [running, canvasId, cascade, enqueue, nodes, connections, patchNode, addToast]);

  const disabled = running || !canvasId || nodeCount === 0;

  return (
    <div
      role="toolbar"
      aria-label="Classic canvas run bar"
      className="pointer-events-auto absolute inset-x-0 bottom-4 mx-auto flex w-fit items-center gap-2 rounded-lg border border-ink-700 bg-ink-900/90 p-1.5 shadow-lg backdrop-blur"
    >
      <button
        type="button"
        onClick={() => void onRun()}
        disabled={disabled}
        className="flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-ink-700 disabled:text-ink-400"
      >
        {running ? (
          <Loader2 size={15} className="animate-spin" aria-hidden="true" />
        ) : (
          <Play size={15} aria-hidden="true" />
        )}
        {running ? 'Running…' : 'Run'}
      </button>
      <span className="px-1 text-xs text-ink-400">
        {nodeCount} {nodeCount === 1 ? 'node' : 'nodes'}
      </span>
    </div>
  );
}
