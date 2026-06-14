/**
 * ClassicMode run bar (Phase 5a — the "Run trigger" last mile).
 *
 * ClassicMode ships a fully-built, tested cascade engine
 * (`runClassicCascade`) but nothing in the UI invokes it. This overlay is
 * that last mile: a single Run control mounted on the classic canvas
 * surface that runs the cascade against the LIVE store graph and wires its
 * two side-channels back into the app:
 *
 *   - `onNodePatch(id, patch)` → `store.patchNode(id, { data: patch })` so
 *     each node's run_status/run_error/run_result render inline, and
 *   - `onToast(message)` → app `useToast().addToast(message, 'error')` so a
 *     contained cascade failure ("Cascade stopped at …") is surfaced.
 *
 * MVP scope: just Run + a running affordance. A node-palette to ADD classic
 * nodes is an explicit LATER slice and is intentionally NOT built here.
 */

import { useCallback, useState } from 'react';
import { Loader2, Play } from 'lucide-react';

import { useToast } from '../../../../components/Toast';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { runClassicCascade } from '../cascade';
import { createClassicBackendRunner } from '../classicRunner';

export interface ClassicRunBarProps {
  /** Injected by tests with a deterministic spy. Defaults to the real
   *  cascade orchestrator at runtime. */
  cascade?: typeof runClassicCascade;
}

export function ClassicRunBar({
  cascade = runClassicCascade,
}: ClassicRunBarProps = {}) {
  const nodes = useCanvasCoreStore((s) => s.nodes);
  const connections = useCanvasCoreStore((s) => s.connections);
  const canvasId = useCanvasCoreStore((s) => s.canvasId);
  const patchNode = useCanvasCoreStore((s) => s.patchNode);
  const { addToast } = useToast();

  const [running, setRunning] = useState(false);

  const nodeCount = nodes.length;

  const onRun = useCallback(async () => {
    // Guard against double-invocation: a click while a cascade is in flight
    // (the button is also disabled, but belt-and-suspenders) and a run with
    // no canvas bound (the backend runner needs a canvas id).
    if (running || !canvasId) return;
    setRunning(true);
    try {
      const runner = createClassicBackendRunner({ canvasId });
      await cascade(nodes, connections, runner, {
        onNodePatch: (id, patch) =>
          patchNode(id, { data: { ...patch } as Record<string, unknown> }),
        onToast: (message) => addToast(message, 'error'),
      });
    } catch (err) {
      // The cascade is contained and should never throw, but be safe: a
      // surprise rejection must not leave the UI stuck or silent.
      const message = err instanceof Error ? err.message : String(err);
      addToast(`Cascade failed: ${message}`, 'error');
    } finally {
      setRunning(false);
    }
  }, [running, canvasId, nodes, connections, cascade, patchNode, addToast]);

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
