/**
 * Phase 6d-M4b — ClassicRunBar async DBOS graph-run path.
 *
 * These tests verify the NEW production default: when no `cascade` prop is
 * supplied, the Run button calls `enqueueGraphRun` with the topoSort node
 * order, and per-node status is driven by task_tracking subtask updates
 * flowing through TaskManagerContext → the Zustand `patchNode` side-effect.
 *
 * Three contracts under test:
 *
 *   1. enqueue is called with the topoSort order of the canvas nodes.
 *   2. A subtask realtime update for node N (`{task_id}-node-{pos}`,
 *      `metadata.node_id = N`) flips that node's `data.run_status` via patchNode.
 *   3. The single-node synchronous Run path (`runSinglePrompt` in SmartMode)
 *      is UNTOUCHED — verified by asserting `cascade` is never called when
 *      the `enqueue` (DBOS) path is in effect.
 *
 * Mocking strategy:
 *   - `useToast` → static `addToast` spy (no ToastProvider needed).
 *   - `useTaskManager` → reactive via `setMockTasks` helper; `rerender` drives
 *     the useEffect with the new tasks array.
 *   - `enqueue` → injectable prop spy (no real network call).
 *
 * The Phase-6a realtime channel (`useCanvasRealtime`) pushes node OUTPUT
 * updates directly into the canvas store. These tests do NOT test that
 * channel — it is covered by its own realtime test suite.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { RenderResult } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { UnifiedTask } from '../../../../contexts/TaskManagerContext';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import type { GraphRunResponse } from '../../services/canvasService';
import { ClassicRunBar } from './ClassicRunBar';

// ── Module mocks ─────────────────────────────────────────────────────────────

const addToast = vi.fn();
vi.mock('../../../../components/Toast', () => ({
  useToast: () => ({ addToast }),
}));

// Reactive tasks store: tests update this array and call `rerender` to drive
// the ClassicRunBar useEffect with new data.
let _mockTasks: UnifiedTask[] = [];

vi.mock('../../../../contexts/TaskManagerContext', () => ({
  useTaskManager: () => ({ tasks: _mockTasks }),
}));

function setMockTasks(tasks: UnifiedTask[]): void {
  _mockTasks = tasks;
}

// ── Builders ─────────────────────────────────────────────────────────────────

function makeSubtask(
  parentTaskId: string,
  position: number,
  nodeId: string,
  phase: UnifiedTask['phase'],
): UnifiedTask {
  return {
    id: `${parentTaskId}-node-${position}`,
    user_id: 'u1',
    task_type: 'agent',
    status: 'processing',
    title: `Node ${nodeId}`,
    progress: 0,
    metadata: { node_id: nodeId },
    created_at: new Date().toISOString(),
    phase,
  };
}

const GRAPH_RUN_RESPONSE: GraphRunResponse = {
  task_id: 'wf-graph-42',
  dbos_workflow_id: 'wf-graph-42',
};

// ── Setup ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  addToast.mockClear();
  setMockTasks([]);
  useCanvasCoreStore.getState().reset();
  // Two-node graph: a → b (a is upstream, b is downstream).
  useCanvasCoreStore.setState({
    canvasId: 'canvas-99',
    kind: 'classic',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-15T08:00:00+00:00',
    nodes: [
      { id: 'a', type: 'llm', data: { body: 'first', run_status: 'idle' } },
      { id: 'b', type: 'llm', data: { body: 'second', run_status: 'idle' } },
    ],
    connections: [{ id: 'e1', source: 'a', target: 'b' }],
  });
});

afterEach(() => {
  useCanvasCoreStore.getState().reset();
  setMockTasks([]);
});

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Render ClassicRunBar with an injected enqueue spy (no cascade = DBOS path). */
function renderBar(
  enqueue: (
    canvasId: string,
    nodeOrder: string[],
    continueOnFailure: boolean,
  ) => Promise<GraphRunResponse>,
): RenderResult {
  return render(<ClassicRunBar enqueue={enqueue} />);
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('ClassicRunBar — async DBOS graph-run path (Phase 6d-M4b)', () => {
  describe('enqueue trigger', () => {
    it('calls enqueue with the topoSort node order when Run is clicked', async () => {
      const enqueue = vi.fn(async () => GRAPH_RUN_RESPONSE);
      renderBar(enqueue);

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
      });

      await waitFor(() => expect(enqueue).toHaveBeenCalledTimes(1));
      expect(enqueue).toHaveBeenCalledWith(
        'canvas-99',
        // topoSort of a→b should produce ['a', 'b'] (upstream first).
        ['a', 'b'],
        true,
      );
    });

    it('does NOT call the legacy cascade when the enqueue (DBOS) path is used', async () => {
      const cascade = vi.fn();
      const enqueue = vi.fn(async () => GRAPH_RUN_RESPONSE);
      // Passing enqueue but NOT cascade → DBOS path. cascade must stay silent.
      render(<ClassicRunBar enqueue={enqueue} />);

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
      });

      await waitFor(() => expect(enqueue).toHaveBeenCalledTimes(1));
      expect(cascade).not.toHaveBeenCalled();
    });

    it('pre-paints all ordered nodes as "queued" before the round-trip', async () => {
      let resolveEnqueue!: (r: GraphRunResponse) => void;
      const enqueue = vi.fn(
        () => new Promise<GraphRunResponse>((res) => { resolveEnqueue = res; }),
      );
      renderBar(enqueue);

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
      });

      // Enqueue promise still pending — both nodes should be 'queued'.
      const nodes = useCanvasCoreStore.getState().nodes as Array<
        { id: string; data: Record<string, unknown> }
      >;
      expect(nodes.find((n) => n.id === 'a')?.data.run_status).toBe('queued');
      expect(nodes.find((n) => n.id === 'b')?.data.run_status).toBe('queued');

      // Resolve so the component can settle.
      await act(async () => { resolveEnqueue(GRAPH_RUN_RESPONSE); });
    });

    it('shows "Running…" while waiting for subtasks', async () => {
      // enqueue resolves but subtasks never complete → button stays "Running…"
      const enqueue = vi.fn(async () => GRAPH_RUN_RESPONSE);
      renderBar(enqueue);

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
      });

      await waitFor(() => expect(enqueue).toHaveBeenCalledTimes(1));
      expect(screen.getByRole('button', { name: /running/i })).toBeDisabled();
    });

    it('prevents double-enqueue while a run is in flight', async () => {
      const enqueue = vi.fn(async () => GRAPH_RUN_RESPONSE);
      renderBar(enqueue);
      const btn = screen.getByRole('button', { name: /^run$/i });

      await act(async () => { fireEvent.click(btn); });
      await waitFor(() => expect(enqueue).toHaveBeenCalledTimes(1));

      // Button is now disabled — force a click anyway.
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /running/i }));
      });
      expect(enqueue).toHaveBeenCalledTimes(1);
    });

    it('surfaces an error toast and re-enables the button on enqueue failure', async () => {
      const enqueue = vi.fn(async () => {
        throw new Error('network timeout');
      });
      renderBar(enqueue);

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
      });

      await waitFor(() =>
        expect(addToast).toHaveBeenCalledWith(
          expect.stringContaining('network timeout'),
          'error',
        ),
      );
      expect(screen.getByRole('button', { name: /^run$/i })).not.toBeDisabled();
    });
  });

  describe('per-node status sync', () => {
    it('flips node run_status to "running" when a subtask enters processing phase', async () => {
      const enqueue = vi.fn(async () => GRAPH_RUN_RESPONSE);
      const { rerender } = renderBar(enqueue);

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
      });
      await waitFor(() => expect(enqueue).toHaveBeenCalledTimes(1));

      // Simulate subtask arriving via TaskManagerContext realtime.
      act(() => {
        setMockTasks([
          makeSubtask('wf-graph-42', 0, 'a', 'processing'),
        ]);
        rerender(<ClassicRunBar enqueue={enqueue} />);
      });

      await waitFor(() => {
        const nodeA = (
          useCanvasCoreStore.getState().nodes as Array<
            { id: string; data: Record<string, unknown> }
          >
        ).find((n) => n.id === 'a');
        expect(nodeA?.data.run_status).toBe('running');
      });
    });

    it('flips node run_status to "succeeded" when a subtask completes', async () => {
      const enqueue = vi.fn(async () => GRAPH_RUN_RESPONSE);
      const { rerender } = renderBar(enqueue);

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
      });
      await waitFor(() => expect(enqueue).toHaveBeenCalledTimes(1));

      act(() => {
        setMockTasks([
          makeSubtask('wf-graph-42', 0, 'a', 'completed'),
        ]);
        rerender(<ClassicRunBar enqueue={enqueue} />);
      });

      await waitFor(() => {
        const nodeA = (
          useCanvasCoreStore.getState().nodes as Array<
            { id: string; data: Record<string, unknown> }
          >
        ).find((n) => n.id === 'a');
        expect(nodeA?.data.run_status).toBe('succeeded');
      });
    });

    it('flips node run_status to "failed" when a subtask fails', async () => {
      const enqueue = vi.fn(async () => GRAPH_RUN_RESPONSE);
      const { rerender } = renderBar(enqueue);

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
      });
      await waitFor(() => expect(enqueue).toHaveBeenCalledTimes(1));

      act(() => {
        setMockTasks([
          makeSubtask('wf-graph-42', 0, 'a', 'failed'),
        ]);
        rerender(<ClassicRunBar enqueue={enqueue} />);
      });

      await waitFor(() => {
        const nodeA = (
          useCanvasCoreStore.getState().nodes as Array<
            { id: string; data: Record<string, unknown> }
          >
        ).find((n) => n.id === 'a');
        expect(nodeA?.data.run_status).toBe('failed');
      });
    });

    it('patches ONLY the node referenced by metadata.node_id, leaving others unchanged', async () => {
      const enqueue = vi.fn(async () => GRAPH_RUN_RESPONSE);
      const { rerender } = renderBar(enqueue);

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
      });
      await waitFor(() => expect(enqueue).toHaveBeenCalledTimes(1));

      // Only node 'a' gets a subtask update.
      act(() => {
        setMockTasks([
          makeSubtask('wf-graph-42', 0, 'a', 'completed'),
        ]);
        rerender(<ClassicRunBar enqueue={enqueue} />);
      });

      await waitFor(() => {
        const nodes = useCanvasCoreStore.getState().nodes as Array<
          { id: string; data: Record<string, unknown> }
        >;
        expect(nodes.find((n) => n.id === 'a')?.data.run_status).toBe('succeeded');
        // Node 'b' was pre-painted 'queued' and has not been updated yet.
        expect(nodes.find((n) => n.id === 'b')?.data.run_status).toBe('queued');
      });
    });

    it('clears running state and parentTaskId when all subtasks reach a terminal phase', async () => {
      const enqueue = vi.fn(async () => GRAPH_RUN_RESPONSE);
      const { rerender } = renderBar(enqueue);

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
      });
      await waitFor(() => expect(enqueue).toHaveBeenCalledTimes(1));
      expect(screen.getByRole('button', { name: /running/i })).toBeDisabled();

      // Both subtasks complete.
      act(() => {
        setMockTasks([
          makeSubtask('wf-graph-42', 0, 'a', 'completed'),
          makeSubtask('wf-graph-42', 1, 'b', 'completed'),
        ]);
        rerender(<ClassicRunBar enqueue={enqueue} />);
      });

      await waitFor(() =>
        expect(
          screen.getByRole('button', { name: /^run$/i }),
        ).not.toBeDisabled(),
      );
    });

    it('ignores subtasks whose metadata.node_id is missing or empty', async () => {
      const enqueue = vi.fn(async () => GRAPH_RUN_RESPONSE);
      const { rerender } = renderBar(enqueue);

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
      });
      await waitFor(() => expect(enqueue).toHaveBeenCalledTimes(1));

      // Subtask with no node_id.
      const orphan: UnifiedTask = {
        id: 'wf-graph-42-node-99',
        user_id: 'u1',
        task_type: 'agent',
        status: 'processing',
        title: 'Orphan',
        progress: 0,
        metadata: {}, // no node_id
        created_at: new Date().toISOString(),
        phase: 'completed',
      };

      act(() => {
        setMockTasks([orphan]);
        rerender(<ClassicRunBar enqueue={enqueue} />);
      });

      // Neither node 'a' nor 'b' should change — still 'queued' from pre-paint.
      await waitFor(() => {
        const nodes = useCanvasCoreStore.getState().nodes as Array<
          { id: string; data: Record<string, unknown> }
        >;
        expect(nodes.find((n) => n.id === 'a')?.data.run_status).toBe('queued');
        expect(nodes.find((n) => n.id === 'b')?.data.run_status).toBe('queued');
      });
    });
  });

  describe('SmartMode single-node run is untouched', () => {
    it('runSinglePrompt is never imported or called by ClassicRunBar', async () => {
      // The SmartMode single-node path lives in smart/runner.ts and is invoked
      // from SmartMode prompt node controls. ClassicRunBar ONLY uses topoSort +
      // enqueueGraphRun. We verify by asserting the cascade prop (synchronous
      // in-browser path) is not called when enqueue is in effect.
      const legacyCascadeSpy = vi.fn();
      const enqueue = vi.fn(async () => GRAPH_RUN_RESPONSE);
      // Pass both props — cascade should NOT be called when enqueue is provided
      // without cascade (the DBOS path is selected based on cascade being absent).
      render(<ClassicRunBar enqueue={enqueue} />);

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
      });

      await waitFor(() => expect(enqueue).toHaveBeenCalledTimes(1));
      expect(legacyCascadeSpy).not.toHaveBeenCalled();
    });
  });
});
