/**
 * Render cost of a drag tick (Wave 1+2, Task 4).
 *
 * Dragging one node must not render the others. Before this task every store
 * change rebuilt the whole React Flow node array (so RF's per-node reference
 * check failed) AND `PromptNodeView` subscribed to the whole `s.nodes` array
 * (so every prompt card re-rendered on every drag frame even when its own
 * inputs were untouched). Both halves are pinned here:
 *
 *   - identity: `toReactFlowNodes` returns the SAME RF object for a canvas
 *     node whose reference and `selected` state did not change;
 *   - render count: a `setNodesDragTick` that moves p1 renders p1 only.
 *
 * React Flow is replaced by a stub that renders each node through the real
 * `nodeTypes` map, exactly as `NodeWrapper` does — the memo wrapping and the
 * per-node store selectors under test are the production ones.
 */

import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CanvasNode } from '../types';

// Render counter — sits INSIDE the component that owns the store
// subscriptions (the real view is invoked as a plain function so its hooks
// belong to this fiber), so a store-driven re-render is observable here.
// `registry.ts` wraps THIS with `memo`, which is what the count measures.
const { renderCounts, counted } = vi.hoisted(() => {
  const map = new Map<string, number>();
  return {
    renderCounts: map,
    counted:
      (real: (props: never) => unknown) =>
      (props: { id: string }) => {
        map.set(props.id, (map.get(props.id) ?? 0) + 1);
        return real(props as never) as React.ReactElement;
      },
  };
});

vi.mock('../smart/nodes/PromptNodeView', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../smart/nodes/PromptNodeView')>();
  return { ...actual, PromptNodeView: counted(actual.PromptNodeView as never) };
});
vi.mock('../smart/nodes/TimelineNodeView', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../smart/nodes/TimelineNodeView')>();
  return {
    ...actual,
    TimelineNodeView: counted(actual.TimelineNodeView as never),
  };
});
vi.mock('../smart/nodes/LoopNodeView', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../smart/nodes/LoopNodeView')>();
  return { ...actual, LoopNodeView: counted(actual.LoopNodeView as never) };
});
vi.mock('../smart/nodes/GroupNodeView', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../smart/nodes/GroupNodeView')>();
  return { ...actual, GroupNodeView: counted(actual.GroupNodeView as never) };
});

vi.mock('../smart/nodes/useGenerationModels', () => ({
  useGenerationModels: () => [],
}));
vi.mock('../smart/nodes/useTextModels', () => ({ useTextModels: () => [] }));
vi.mock('../smart/nodes/useAgents', () => ({ useAgents: () => [] }));

vi.mock('@xyflow/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@xyflow/react')>();
  type StubNode = {
    id: string;
    type?: string;
    data?: unknown;
    selected?: boolean;
    position: { x: number; y: number };
  };
  return {
    ...actual,
    ReactFlow: (props: Record<string, unknown>) => {
      const nodeTypes = (props.nodeTypes ?? {}) as Record<
        string,
        React.ComponentType<Record<string, unknown>>
      >;
      const nodes = (props.nodes ?? []) as StubNode[];
      return (
        <>
          {nodes.map((n) => {
            const View = n.type ? nodeTypes[n.type] : undefined;
            if (!View) return null;
            // The same prop surface `NodeWrapper` hands a view — the
            // position props are what make the DRAGGED card re-render
            // (and, being per-node, what must leave the others alone).
            return (
              <View
                key={n.id}
                id={n.id}
                type={n.type}
                data={n.data}
                selected={!!n.selected}
                positionAbsoluteX={n.position.x}
                positionAbsoluteY={n.position.y}
              />
            );
          })}
        </>
      );
    },
  };
});

import { ReactFlowProvider } from '@xyflow/react';

import { CanvasSurface, toReactFlowNodes } from './CanvasSurface';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

const promptNode = (id: string, x: number): CanvasNode =>
  ({
    id,
    type: 'prompt',
    position: { x, y: 0 },
    data: {
      body: `body ${id}`,
      run_status: 'idle',
      provider_slug: '',
      agent_id: null,
      resource_refs: [],
    },
  }) as unknown as CanvasNode;

const threePromptNodes = (): CanvasNode[] => [
  promptNode('p1', 0),
  promptNode('p2', 400),
  promptNode('p3', 800),
];

/** Exactly what `applyNodeChanges` produces for a mid-drag position tick:
 *  a new array, a new object for the dragged node only. */
function moveNode(
  nodes: CanvasNode[],
  id: string,
  position: { x: number; y: number },
): CanvasNode[] {
  return nodes.map((n) =>
    (n as unknown as { id: string }).id === id
      ? ({ ...(n as object), position } as unknown as CanvasNode)
      : n,
  );
}

afterEach(() => {
  cleanup();
  renderCounts.clear();
  useCanvasCoreStore.getState().reset();
});

describe('toReactFlowNodes identity', () => {
  it('keeps the RF object identity for nodes whose reference and selection are unchanged', () => {
    const nodes = threePromptNodes();
    const sel: ReadonlySet<string> = new Set<string>();

    const a = toReactFlowNodes(nodes, sel);
    // Same node references, brand-new array — the drag-tick shape.
    const b = toReactFlowNodes([...nodes], sel);

    expect(b[1]).toBe(a[1]);
    expect(b[2]).toBe(a[2]);
  });

  it('mints a fresh RF object when the node reference changed', () => {
    const nodes = threePromptNodes();
    const sel: ReadonlySet<string> = new Set<string>();
    const a = toReactFlowNodes(nodes, sel);

    const moved = moveNode(nodes, 'p1', { x: 10, y: 10 });
    const b = toReactFlowNodes(moved, sel);

    expect(b[0]).not.toBe(a[0]);
    expect(b[0].position).toEqual({ x: 10, y: 10 });
    expect(b[1]).toBe(a[1]);
  });

  it('mints a fresh RF object when only the selection changed', () => {
    const nodes = threePromptNodes();
    const a = toReactFlowNodes(nodes, new Set<string>());
    const b = toReactFlowNodes(nodes, new Set(['p2']));

    expect(b[1]).not.toBe(a[1]);
    expect(b[1].selected).toBe(true);
    expect(b[0]).toBe(a[0]);
  });
});

describe('drag-tick render cost', () => {
  beforeEach(() => {
    useCanvasCoreStore.setState({
      canvasId: 'c1',
      kind: 'smart',
      nodes: threePromptNodes() as never,
      connections: [] as never,
      selection: [],
    });
  });

  it('a drag tick on node p1 renders p1 only', () => {
    render(
      <ReactFlowProvider>
        <CanvasSurface />
      </ReactFlowProvider>,
    );
    expect(renderCounts.get('p2')).toBeGreaterThanOrEqual(1);
    renderCounts.clear();

    act(() => {
      const live = useCanvasCoreStore.getState().nodes as CanvasNode[];
      useCanvasCoreStore
        .getState()
        .setNodesDragTick(moveNode(live, 'p1', { x: 10, y: 10 }));
    });

    expect(renderCounts.get('p1') ?? 0).toBe(1);
    expect(renderCounts.get('p2') ?? 0).toBe(0);
    expect(renderCounts.get('p3') ?? 0).toBe(0);
  });

  it('ten drag ticks on p1 leave the other prompt cards at zero renders', () => {
    render(
      <ReactFlowProvider>
        <CanvasSurface />
      </ReactFlowProvider>,
    );
    renderCounts.clear();

    for (let i = 1; i <= 10; i += 1) {
      act(() => {
        const live = useCanvasCoreStore.getState().nodes as CanvasNode[];
        useCanvasCoreStore
          .getState()
          .setNodesDragTick(moveNode(live, 'p1', { x: i * 10, y: 0 }));
      });
    }

    expect(renderCounts.get('p1') ?? 0).toBe(10);
    expect(renderCounts.get('p2') ?? 0).toBe(0);
    expect(renderCounts.get('p3') ?? 0).toBe(0);
  });
});

/**
 * The three other views that derived from the WHOLE graph (`resolveSourceUrls`
 * for the loop/timeline input strip, `groupSummary`/`groupPreviewItems` for
 * the group card). They subscribed to `s.nodes` exactly as the prompt card
 * did, so a canvas holding any of them still burned a full render pass per
 * drag frame — `memo` cannot help against a store subscription.
 */
describe('drag-tick render cost — mixed canvas', () => {
  const mixedNodes = (): CanvasNode[] =>
    [
      promptNode('p1', 0),
      {
        id: 't1',
        type: 'timeline',
        position: { x: 400, y: 0 },
        data: {
          segments: [{ id: 'seg1', prompt: 'opening', seconds: 5 }],
          model: '',
          aspect: '16:9',
          run_status: 'idle',
        },
      },
      {
        id: 'l1',
        type: 'loop',
        position: { x: 800, y: 0 },
        data: {
          mode: 'serial',
          label: '',
          rounds: 2,
          round_start: 1,
          prompts: [''],
        },
      },
      {
        id: 'g1',
        type: 'group',
        position: { x: 1200, y: 0 },
        data: { label: 'Group', items: [] },
      },
    ] as unknown as CanvasNode[];

  beforeEach(() => {
    useCanvasCoreStore.setState({
      canvasId: 'c1',
      kind: 'smart',
      nodes: mixedNodes() as never,
      connections: [] as never,
      selection: [],
    });
  });

  it('moving the prompt leaves the timeline / loop / group cards at zero renders', () => {
    render(
      <ReactFlowProvider>
        <CanvasSurface />
      </ReactFlowProvider>,
    );
    expect(renderCounts.get('t1')).toBeGreaterThanOrEqual(1);
    expect(renderCounts.get('l1')).toBeGreaterThanOrEqual(1);
    expect(renderCounts.get('g1')).toBeGreaterThanOrEqual(1);
    renderCounts.clear();

    for (let i = 1; i <= 5; i += 1) {
      act(() => {
        const live = useCanvasCoreStore.getState().nodes as CanvasNode[];
        useCanvasCoreStore
          .getState()
          .setNodesDragTick(moveNode(live, 'p1', { x: i * 10, y: 0 }));
      });
    }

    expect(renderCounts.get('p1') ?? 0).toBe(5);
    expect(renderCounts.get('t1') ?? 0).toBe(0);
    expect(renderCounts.get('l1') ?? 0).toBe(0);
    expect(renderCounts.get('g1') ?? 0).toBe(0);
  });
});
