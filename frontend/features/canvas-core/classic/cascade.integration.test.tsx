/**
 * ClassicMode vertical-slice INTEGRATION ACCEPTANCE (Phase 5a W2).
 *
 * This is the gate that proves the 5a engine end-to-end BEFORE W3 adds more
 * node types. It wires the REAL pieces together:
 *
 *   store (canvasCoreStore) ── nodes/connections ──▶ runClassicCascade
 *        ▲                                                  │
 *        └──────── patchNode(id, {data}) ◀── onNodePatch ───┘
 *
 *   post-run store state ─▶ CLASSIC_NODE_TYPES views (rendered reflection)
 *
 * ONLY the network transport (`ClassicRunner`) is mocked — everything else
 * (dispatch table, topo order, downstream-blocked propagation, the store's
 * patch-into-data merge, and the node views' tone rendering) is the real
 * production code.
 *
 * ── Discovered semantics (NOT a bug — documented design) ───────────────────
 * `classicDispatch.ts` classifies `image` and `output` as PASSIVE source/sink
 * node types: they hold/collect data and are NEVER dispatched to a provider.
 * Only `llm` and `comfy` are runnable. So in `image → llm → comfy → output`
 * the runnable spine is llm → comfy; the passive endpoints are pass-through
 * (they end `skipped`, staying `idle`), they are NEVER `failed`/`blocked` on
 * the happy path. We assert the engine's REAL contract rather than forcing
 * passive nodes to "succeed" (that would break 6 existing cascade tests and
 * contradict the dispatch design). When comfy fails, `output` — being
 * DOWNSTREAM of the failure — is still correctly marked `blocked`.
 */

import { ReactFlowProvider } from '@xyflow/react';
import { render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { runClassicCascade, type CascadeHandlers } from './cascade';
import type { ClassicRunner } from './classicRunner';
import { clearAllAbortControllers } from './abortRegistry';
import { CLASSIC_NODE_TYPES } from './ClassicNodeViews';
import { getClassicNodeDefinition } from './registry';
import { createCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasConnection, CanvasNode } from '../types';

// ---------------------------------------------------------------------------
// Builders — a real classic graph image → llm → comfy → output
// ---------------------------------------------------------------------------

function classicNode(
  id: string,
  type: string,
  data: Record<string, unknown> = {},
): CanvasNode {
  // comfy needs a workflow_slug to be runnable (else dispatch = invalid).
  const base = type === 'comfy' ? { workflow_slug: `wf-${id}` } : {};
  return {
    id,
    type,
    position: { x: 0, y: 0 },
    data: { run_status: 'idle', run_started_at: null, run_error: null, ...base, ...data },
  };
}

function edge(source: string, target: string): CanvasConnection {
  return { id: `${source}->${target}`, source, target };
}

const COMFY_LABEL = 'Comfy Render';

/** The 4-node / 3-edge vertical slice the plan requires. */
function seedSliceGraph(): { nodes: CanvasNode[]; connections: CanvasConnection[] } {
  return {
    nodes: [
      classicNode('image', 'image'),
      classicNode('llm', 'llm', { model: 'qwen-max' }),
      classicNode('comfy', 'comfy', { label: COMFY_LABEL }),
      classicNode('output', 'output'),
    ],
    connections: [edge('image', 'llm'), edge('llm', 'comfy'), edge('comfy', 'output')],
  };
}

const SLICE_EDGES = [
  { source: 'image', target: 'llm' },
  { source: 'llm', target: 'comfy' },
  { source: 'comfy', target: 'output' },
];

// ---------------------------------------------------------------------------
// A real store wired to the cascade, with the network save stubbed out.
// ---------------------------------------------------------------------------

type Store = ReturnType<typeof createCanvasCoreStore>;

function makeWiredStore(): Store {
  // Real store, only the persist transport is a no-op so patchNode never hits
  // the network; huge debounce so the timer never fires inside the test.
  return createCanvasCoreStore({
    debounceMs: 10_000_000,
    saveImpl: async (_id, _payload) => ({
      ok: true as const,
      canvas: { base_updated_at: '2026-06-14T00:00:00Z' } as never,
    }),
  });
}

function seedStore(store: Store): void {
  const { nodes, connections } = seedSliceGraph();
  store.setState({
    canvasId: 'canvas-5a',
    kind: 'classic',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-14T00:00:00Z',
    nodes,
    connections,
  });
}

function dataOf(store: Store, id: string): Record<string, unknown> {
  const node = store
    .getState()
    .nodes.find((n) => (n as Record<string, unknown>).id === id) as
    | Record<string, unknown>
    | undefined;
  return (node?.data as Record<string, unknown>) ?? {};
}

interface Wiring {
  handlers: CascadeHandlers;
  toasts: string[];
  runningOrder: string[];
}

function wireHandlers(store: Store): Wiring {
  const toasts: string[] = [];
  const runningOrder: string[] = [];
  const handlers: CascadeHandlers = {
    onNodePatch: (id, patch) => {
      if (patch.run_status === 'running') runningOrder.push(id);
      // The exact wiring the surface uses: cascade fields merge into node.data.
      // (`CascadeNodeStatusPatch` is an interface; spread into a plain object
      // literal so it satisfies the store's `Record<string, unknown>` param.)
      store.getState().patchNode(id, { data: { ...patch } });
    },
    onToast: (msg) => toasts.push(msg),
    now: () => '2026-06-14T00:00:00Z',
  };
  return { handlers, toasts, runningOrder };
}

/** Assert `observed` does not violate any edge whose endpoints both appear
 *  in it — i.e. it is a valid topological order of the dispatched subgraph. */
function assertValidTopoOrder(
  observed: string[],
  edges: { source: string; target: string }[],
): void {
  const pos = new Map(observed.map((id, i) => [id, i]));
  for (const { source, target } of edges) {
    if (pos.has(source) && pos.has(target)) {
      expect(pos.get(source)!).toBeLessThan(pos.get(target)!);
    }
  }
}

// ---------------------------------------------------------------------------
// Rendered-reflection helper — render a single classic node view (the
// jsdom-safe pattern the repo already uses in ClassicNodeViews.test.tsx;
// the full React Flow surface needs real layout/ResizeObserver which jsdom
// lacks). We feed each view the POST-cascade store data so we prove the
// store-state → view-tone path, not a hand-built fixture.
// ---------------------------------------------------------------------------

const baseNodeProps = {
  dragHandle: undefined,
  draggable: true,
  selectable: true,
  deletable: true,
  selected: false,
  dragging: false,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  width: 170,
  height: 100,
  zIndex: 0,
} as const;

function Wrap({ children }: { children: ReactNode }) {
  return <ReactFlowProvider>{children}</ReactFlowProvider>;
}

function renderClassicNode(
  type: keyof typeof CLASSIC_NODE_TYPES,
  data: Record<string, unknown>,
) {
  const View = CLASSIC_NODE_TYPES[type];
  return render(
    <Wrap>
      <View {...baseNodeProps} id={`${type}-1`} type={type} data={data} selected={false} />
    </Wrap>,
  );
}

function handleIds(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('.react-flow__handle'))
    .map((el) => el.getAttribute('data-handleid'))
    .filter((id): id is string => Boolean(id))
    .sort();
}

// ===========================================================================

let store: Store;

beforeEach(() => {
  store = makeWiredStore();
  seedStore(store);
});

afterEach(() => {
  store.getState().reset(); // clears the (huge) debounce timer
  clearAllAbortControllers();
});

describe('ClassicMode vertical slice — full chain success (image → llm → comfy → output)', () => {
  it('runs the runnable spine in topo order, passive endpoints pass through, NONE blocked', async () => {
    const ran: string[] = [];
    const okRunner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return { ok: true, text: `out:${ctx.nodeId}`, error: null };
    };
    const { handlers, toasts, runningOrder } = wireHandlers(store);

    const report = await runClassicCascade(
      store.getState().nodes,
      store.getState().connections,
      okRunner,
      handlers,
    );

    // ── CASCADE ASSERTION (success path) ──────────────────────────────────
    // Runnable spine succeeded; passive source/sink are pass-through; the
    // chain completed with ZERO failed and ZERO blocked.
    expect(report.succeeded.sort()).toEqual(['comfy', 'llm']);
    expect(report.failed).toEqual([]);
    expect(report.blocked).toEqual([]);
    expect(report.skipped.sort()).toEqual(['image', 'output']);
    expect(toasts).toEqual([]);
    // ──────────────────────────────────────────────────────────────────────

    // Topo order respected: the dispatched nodes ran llm-before-comfy, a
    // valid topological order of image→llm→comfy→output (no edge violated).
    expect(ran).toEqual(['llm', 'comfy']);
    assertValidTopoOrder(ran, SLICE_EDGES);
    assertValidTopoOrder(runningOrder, SLICE_EDGES);

    // The REAL store reflects it: runnable nodes succeeded, passive endpoints
    // never went failed/blocked (they stayed idle = pass-through).
    expect(dataOf(store, 'llm').run_status).toBe('succeeded');
    expect(dataOf(store, 'comfy').run_status).toBe('succeeded');
    expect(dataOf(store, 'image').run_status).toBe('idle');
    expect(dataOf(store, 'output').run_status).toBe('idle');
    for (const id of ['image', 'llm', 'comfy', 'output']) {
      expect(dataOf(store, id).run_status).not.toBe('blocked');
    }
  });
});

describe('ClassicMode vertical slice — mid-chain comfy failure → downstream blocked + toast', () => {
  it('marks comfy failed with its run_error, output blocked, and toasts the failure (never silent)', async () => {
    const COMFY_ERROR = 'ComfyUI workflow failed: node 7 CUDA OOM';
    const ran: string[] = [];
    const failingRunner: ClassicRunner = async (ctx) => {
      ran.push(ctx.nodeId);
      return ctx.nodeId === 'comfy'
        ? { ok: false, text: '', error: COMFY_ERROR }
        : { ok: true, text: `out:${ctx.nodeId}`, error: null };
    };
    const { handlers, toasts } = wireHandlers(store);

    const report = await runClassicCascade(
      store.getState().nodes,
      store.getState().connections,
      failingRunner,
      handlers,
    );

    // ── CASCADE ASSERTION (comfy-failure → blocked + toast) ────────────────
    // ENG-CRITICAL: the comfy failure is CONTAINED + VISIBLE, never silent.
    expect(report.succeeded).toEqual(['llm']); // upstream llm still succeeded
    expect(report.failed).toEqual(['comfy']); // comfy is the failed node
    expect(report.blocked).toEqual(['output']); // downstream of comfy → blocked
    expect(toasts).toContain(`Cascade stopped at ${COMFY_LABEL}`);
    // ──────────────────────────────────────────────────────────────────────

    // comfy's downstream (output) was NEVER dispatched.
    expect(ran).toEqual(['llm', 'comfy']);

    // The REAL store carries the failure end-to-end.
    expect(dataOf(store, 'llm').run_status).toBe('succeeded');
    expect(dataOf(store, 'comfy').run_status).toBe('failed');
    expect(dataOf(store, 'comfy').run_error).toBe(COMFY_ERROR);
    expect(dataOf(store, 'output').run_status).toBe('blocked');
    // image (passive, upstream of the failure) is untouched — not blocked.
    expect(dataOf(store, 'image').run_status).toBe('idle');
  });
});

describe('ClassicMode vertical slice — rendered reflection (views show the failed/blocked state)', () => {
  it('renders comfy (failed tone + inline run_error) and output (blocked tone) from POST-cascade store data', async () => {
    const COMFY_ERROR = 'ComfyUI workflow failed: node 7 CUDA OOM';
    const failingRunner: ClassicRunner = async (ctx) =>
      ctx.nodeId === 'comfy'
        ? { ok: false, text: '', error: COMFY_ERROR }
        : { ok: true, text: 'ok', error: null };
    const { handlers } = wireHandlers(store);

    // Drive the engine so the store holds the post-failure state, THEN render
    // the views off that exact state (store → view, not a hand-built fixture).
    await runClassicCascade(
      store.getState().nodes,
      store.getState().connections,
      failingRunner,
      handlers,
    );

    // --- comfy view: failed tone + inline run_error text -------------------
    const { container: comfyContainer } = renderClassicNode(
      'comfy',
      dataOf(store, 'comfy'),
    );
    const comfyNode = screen.getByTestId('classic-node-comfy');
    expect(comfyNode.className).toContain('border-rose-500'); // RUN_STATUS_TONE.failed
    expect(screen.getByTestId('classic-node-comfy-error')).toHaveTextContent(
      COMFY_ERROR,
    );
    // Located via the registry handle ids (typed ports) — proves the wired
    // node is the comfy definition, not a stand-in.
    const comfyDef = getClassicNodeDefinition('comfy')!;
    const expectedComfyHandles = [...comfyDef.inputs, ...comfyDef.outputs]
      .map((p) => p.id)
      .sort();
    expect(handleIds(comfyContainer)).toEqual(expectedComfyHandles);

    // --- output view: blocked tone (RUN_STATUS_TONE.blocked) ---------------
    renderClassicNode('output', dataOf(store, 'output'));
    const outputNode = screen.getByTestId('classic-node-output');
    expect(outputNode.className).toContain('border-ink-400');
    expect(outputNode.className).toContain('opacity-60');
    // blocked is inert, not its own error → no error row.
    expect(
      within(outputNode).queryByTestId('classic-node-output-error'),
    ).toBeNull();

    // --- llm view: succeeded tone, sanity that the spine rendered green ----
    renderClassicNode('llm', dataOf(store, 'llm'));
    const llmNode = screen.getByTestId('classic-node-llm');
    expect(llmNode.className).toContain('border-emerald-500'); // RUN_STATUS_TONE.succeeded
  });
});
