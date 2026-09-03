// features/canvas-core/smart/nodes/GroupNodeView.selectorCost.test.tsx
//
// Per-tick selector cost of the group card (canvas fluency, final review #5).
//
// Zustand runs every selector on every `set`, so anything a selector computes
// happens sixty times a second during a drag, per group card, on the critical
// path of the gesture this branch exists to make smooth. The group card used
// to select `JSON.stringify(groupPreviewItems(s.nodes, id))` — walking the
// node array, allocating a fresh LightboxItem per image, then serialising the
// lot. The signature was stable so nothing RE-RENDERED, which is why the
// render-count acceptance passed while the work was still being done.
//
// The fix subscribes to the contributing node REFERENCES instead
// (`groupPreviewSources` + `useShallow`): a drag tick replaces only the node
// that moved (see moveNode below — exactly what applyNodeChanges produces),
// so the shallow compare holds and the derivation is skipped entirely.

import { ReactFlowProvider } from '@xyflow/react';
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));

vi.mock('../grouping', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../grouping')>();
  return { ...actual, groupPreviewItems: vi.fn(actual.groupPreviewItems) };
});

import { groupPreviewItems } from '../grouping';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import type { CanvasNode } from '../../types';
import { GroupNodeView } from './GroupNodeView';

const derive = groupPreviewItems as unknown as ReturnType<typeof vi.fn>;

const baseProps = {
  selected: false,
  dragging: false,
  zIndex: 0,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  deletable: true,
  draggable: true,
  selectable: true,
} as const;

const IMG = (n: number) => ({
  url: `/api/v1/generated-media/${n}/cover`,
  kind: 'image',
  name: `img${n}`,
});

/** A group holding two images, one child media node, and an unrelated node
 *  that is the one being dragged. */
const nodes = (): CanvasNode[] =>
  [
    {
      id: 'g1',
      type: 'group',
      position: { x: 0, y: 0 },
      data: { label: 'Group', items: [IMG(1), IMG(2)] },
    },
    {
      id: 'm1',
      type: 'media',
      parentId: 'g1',
      position: { x: 10, y: 10 },
      data: { title: 'Child', items: [IMG(3)] },
    },
    { id: 'p1', type: 'prompt', position: { x: 900, y: 0 }, data: { text: 'hi' } },
  ] as unknown as CanvasNode[];

/** Exactly what applyNodeChanges produces mid-drag: a new array, a new object
 *  for the dragged node only. */
function moveNode(list: CanvasNode[], id: string, x: number): CanvasNode[] {
  return list.map((n) =>
    (n as unknown as { id: string }).id === id
      ? ({ ...(n as object), position: { x, y: 0 } } as unknown as CanvasNode)
      : n,
  );
}

beforeEach(() => {
  useCanvasCoreStore.setState({
    canvasId: 'c1',
    kind: 'smart',
    nodes: nodes() as never,
    connections: [] as never,
    selection: [],
  });
});

afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
  derive.mockClear();
});

describe('GroupNodeView — the preview derivation is not per-tick work', () => {
  function mount() {
    return render(
      <ReactFlowProvider>
        <GroupNodeView {...baseProps} id="g1" type="group" data={{ label: 'Group', items: [IMG(1), IMG(2)] }} />
      </ReactFlowProvider>,
    );
  }

  it('ten drag ticks on an unrelated node derive the preview zero times', () => {
    mount();
    derive.mockClear();

    for (let i = 1; i <= 10; i += 1) {
      act(() => {
        const live = useCanvasCoreStore.getState().nodes as CanvasNode[];
        useCanvasCoreStore.getState().setNodesDragTick(moveNode(live, 'p1', i * 10));
      });
    }

    expect(derive).toHaveBeenCalledTimes(0);
  });

  it('still recomputes when a member node actually changes its images', () => {
    // The cheap path must not become a stale path.
    mount();
    derive.mockClear();

    act(() => {
      const live = useCanvasCoreStore.getState().nodes as CanvasNode[];
      useCanvasCoreStore.getState().setNodes(
        live.map((n) =>
          (n as unknown as { id: string }).id === 'm1'
            ? ({ ...(n as object), data: { title: 'Child', items: [IMG(3), IMG(4)] } } as unknown as CanvasNode)
            : n,
        ) as never,
      );
    });

    expect(derive.mock.calls.length).toBeGreaterThan(0);
  });
});
