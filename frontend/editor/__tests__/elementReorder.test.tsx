import { useRef, useState } from 'react';
import { render, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ElementOp, SceneDoc } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// Deterministic ids + stubbed meta save (mirrors SceneBlock.test.tsx).
const svc = vi.hoisted(() => {
  let n = 0;
  return {
    updateSceneMeta: vi.fn().mockResolvedValue({}),
    nextId: () => `el_${(n++).toString(16).padStart(8, '0')}`,
  };
});
vi.mock('../sceneService', () => ({
  newElementId: () => svc.nextId(),
  updateSceneMeta: svc.updateSceneMeta,
}));

// Stateful useSceneSync mock: dispatchOps records the ops and applies the
// optimistic list so the reordered rows actually re-render.
const sync = vi.hoisted(() => ({
  dispatch: vi.fn(),
  reconcile: vi.fn(),
  applyRemoteOps: vi.fn(),
}));
vi.mock('../useSceneSync', async () => {
  const React = await import('react');
  return {
    useSceneSync: (scene: SceneDoc) => {
      const [elements, setElements] = React.useState(scene.elements);
      return {
        elements,
        version: scene.content_version,
        saveState: 'saved' as const,
        conflict: null,
        dispatchOps: (ops: ElementOp[], optimistic: SceneDoc['elements']) => {
          sync.dispatch(ops, optimistic);
          setElements(optimistic);
        },
        resolveConflict: () => {},
        applyRemoteOps: sync.applyRemoteOps,
        reconcile: sync.reconcile,
        flush: async () => {},
      };
    },
  };
});

import { SceneBlock } from '../components/SceneBlock';

const makeScene = (elements: SceneDoc['elements']): SceneDoc => ({
  id: '900',
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Blank Studio',
  time_of_day: 'NIGHT',
  content_version: 1,
  sort_order: 0,
  elements,
});

// A fake dataTransfer (jsdom's DnD has none) — matches reorderAndFallback.test.tsx.
const fakeDataTransfer = () => ({ setData: vi.fn(), getData: () => '', effectAllowed: '' });

const rowOf = (elementId: string): HTMLElement =>
  document.querySelector(`[data-el-id="${elementId}"]`)!.closest('.mh-el-row') as HTMLElement;

// Mock a row's rect so elementEdgeFromPointer resolves a deterministic edge
// (jsdom returns an all-zero rect otherwise). Midpoint = 20.
const stubRect = (el: HTMLElement) => {
  el.getBoundingClientRect = () =>
    ({ top: 0, bottom: 40, height: 40, left: 0, right: 0, width: 0, x: 0, y: 0, toJSON() {} }) as DOMRect;
};

// @testing-library's fireEvent does NOT forward clientY onto jsdom drag events
// (they arrive undefined → the edge math always reads 'bottom'). Dispatch a
// native bubbling event with clientY set as an own property — React reads it
// straight off the native event, so the pointer edge is honoured.
const fireDragAt = (node: HTMLElement, type: 'dragover' | 'drop', clientY: number) => {
  const evt = new Event(type, { bubbles: true, cancelable: true });
  Object.assign(evt, { clientY, dataTransfer: fakeDataTransfer() });
  node.dispatchEvent(evt);
};

afterEach(() => {
  cleanup();
  sync.dispatch.mockClear();
  sync.reconcile.mockClear();
  sync.applyRemoteOps.mockClear();
  svc.updateSceneMeta.mockClear();
});

const threeElements = (): SceneDoc['elements'] => [
  { id: 'el_a', type: 'action', text: 'Alpha.' },
  { id: 'el_b', type: 'action', text: 'Bravo.' },
  { id: 'el_c', type: 'action', text: 'Charlie.' },
];

describe('SceneBlock element hover-gutter reorder', () => {
  it('renders a block number and a 4-dot drag handle per element row', () => {
    // Continuous document-order numbering (A1): the scene heading consumes
    // block 1 (blockIndexBase defaults to 0), so this scene's elements start
    // at 2, not 1.
    render(<SceneBlock scene={makeScene(threeElements())} index={0} />);
    const nums = Array.from(document.querySelectorAll('.mh-el-num')).map((n) => n.textContent);
    expect(nums).toEqual(['2', '3', '4']);
    const handles = document.querySelectorAll('.mh-el-drag');
    expect(handles).toHaveLength(3);
    // 4 dots per handle (2×2 — six read as too busy).
    expect(handles[0].querySelectorAll('.mh-el-dot')).toHaveLength(4);
    expect(handles[0]).toHaveAttribute('aria-label', 'Drag to reorder');
    expect(handles[0]).toHaveAttribute('draggable', 'true');
  });

  it('dragging element C onto element A top edge dispatches move { before_id: A }', () => {
    render(<SceneBlock scene={makeScene(threeElements())} index={0} />);

    const cHandle = rowOf('el_c').querySelector('.mh-el-drag') as HTMLElement;
    const aRow = rowOf('el_a');
    stubRect(aRow);

    fireEvent.dragStart(cHandle, { dataTransfer: fakeDataTransfer() });
    // clientY 5 is above the row midpoint (20) → 'top' → land before A.
    fireDragAt(aRow, 'dragover', 5);
    fireDragAt(aRow, 'drop', 5);

    expect(sync.dispatch).toHaveBeenCalledTimes(1);
    const [ops] = sync.dispatch.mock.calls[0];
    expect(ops).toEqual([{ op: 'move', element_id: 'el_c', before_id: 'el_a' }]);

    // Optimistic list re-anchored: C now sits first.
    const [, optimistic] = sync.dispatch.mock.calls[0];
    expect((optimistic as SceneDoc['elements']).map((e) => e.id)).toEqual([
      'el_c',
      'el_a',
      'el_b',
    ]);
  });

  it('dropping onto the bottom edge dispatches move { after_id: target }', () => {
    render(<SceneBlock scene={makeScene(threeElements())} index={0} />);

    const aHandle = rowOf('el_a').querySelector('.mh-el-drag') as HTMLElement;
    const bRow = rowOf('el_b');
    stubRect(bRow);

    fireEvent.dragStart(aHandle, { dataTransfer: fakeDataTransfer() });
    // clientY 30 is below the midpoint (20) → 'bottom' → land after B.
    fireDragAt(bRow, 'dragover', 30);
    fireDragAt(bRow, 'drop', 30);

    const [ops] = sync.dispatch.mock.calls[0];
    expect(ops).toEqual([{ op: 'move', element_id: 'el_a', after_id: 'el_b' }]);
  });

  it('dropping an element onto itself is a no-op (no dispatch)', () => {
    render(<SceneBlock scene={makeScene(threeElements())} index={0} />);

    const bHandle = rowOf('el_b').querySelector('.mh-el-drag') as HTMLElement;
    const bRow = rowOf('el_b');
    stubRect(bRow);

    fireEvent.dragStart(bHandle, { dataTransfer: fakeDataTransfer() });
    fireDragAt(bRow, 'dragover', 5);
    fireDragAt(bRow, 'drop', 5);

    expect(sync.dispatch).not.toHaveBeenCalled();
  });
});

// ── Cross-scene paragraph drag ───────────────────────────────────────────────
// A shell-like harness: holds the broadcast elementDrag state + the external-ops
// registry, exactly as EditorShell wires them, and renders TWO SceneBlocks.
function TwoScenes({
  sceneA,
  sceneB,
}: {
  sceneA: SceneDoc;
  sceneB: SceneDoc;
}) {
  const [elementDrag, setElementDrag] = useState<{
    sceneId: string;
    element: SceneDoc['elements'][number];
  } | null>(null);
  const reg = useRef(new Map<string, (ops: ElementOp[]) => void>());
  const shared = {
    elementDrag,
    onElementDragBegin: (sceneId: string, element: SceneDoc['elements'][number]) =>
      setElementDrag({ sceneId, element }),
    onElementDragDone: () => setElementDrag(null),
    onCrossSceneDelete: (sceneId: string, elementId: string) =>
      reg.current.get(sceneId)?.([{ op: 'delete', element_id: elementId }]),
    onRegisterExternalOps: (sceneId: string, fn: ((ops: ElementOp[]) => void) | null) => {
      if (fn) reg.current.set(sceneId, fn);
      else reg.current.delete(sceneId);
    },
  };
  return (
    <>
      <SceneBlock scene={sceneA} index={0} {...shared} />
      <SceneBlock scene={sceneB} index={1} blockIndexBase={4} {...shared} />
    </>
  );
}

describe('cross-scene paragraph drag', () => {
  const sceneA = (): SceneDoc => ({ ...makeScene(threeElements()), id: '900' });
  const sceneB = (elements: SceneDoc['elements']): SceneDoc => ({
    ...makeScene(elements),
    id: '901',
  });

  it('dropping onto another scene inserts there (full payload) then deletes from the source', () => {
    render(
      <TwoScenes
        sceneA={sceneA()}
        sceneB={sceneB([{ id: 'el_x', type: 'action', text: 'X-ray.' }])}
      />,
    );

    const aHandle = rowOf('el_a').querySelector('.mh-el-drag') as HTMLElement;
    const xRow = rowOf('el_x');
    stubRect(xRow);

    fireEvent.dragStart(aHandle, { dataTransfer: fakeDataTransfer() });
    fireDragAt(xRow, 'dragover', 5); // above midpoint → 'top' → before el_x
    fireDragAt(xRow, 'drop', 5);

    const allOps = sync.dispatch.mock.calls.map(([ops]) => ops[0]);
    const insert = allOps.find((op: ElementOp) => op.op === 'insert');
    expect(insert).toMatchObject({
      op: 'insert',
      element_id: 'el_a',
      before_id: 'el_x',
      payload: { type: 'action', text: 'Alpha.' },
    });
    const del = allOps.find((op: ElementOp) => op.op === 'delete');
    expect(del).toMatchObject({ op: 'delete', element_id: 'el_a' });
    // Insert lands BEFORE the source delete (a failure duplicates, never loses).
    expect(allOps.indexOf(insert!)).toBeLessThan(allOps.indexOf(del!));
    // No same-scene move op was involved.
    expect(allOps.some((op: ElementOp) => op.op === 'move')).toBe(false);
  });

  it('dropping on an EMPTY scene heading row appends the paragraph there', () => {
    render(<TwoScenes sceneA={sceneA()} sceneB={sceneB([])} />);

    const aHandle = rowOf('el_a').querySelector('.mh-el-drag') as HTMLElement;
    const headrows = document.querySelectorAll('.mh-scene-headrow');
    const bHead = headrows[1] as HTMLElement;

    fireEvent.dragStart(aHandle, { dataTransfer: fakeDataTransfer() });
    fireEvent.dragOver(bHead, { dataTransfer: fakeDataTransfer() });
    fireEvent.drop(bHead, { dataTransfer: fakeDataTransfer() });

    const allOps = sync.dispatch.mock.calls.map(([ops]) => ops[0]);
    const insert = allOps.find((op: ElementOp) => op.op === 'insert');
    // Empty scene → plain append: no before_id / after_id anchor.
    expect(insert).toMatchObject({
      op: 'insert',
      element_id: 'el_a',
      payload: { type: 'action', text: 'Alpha.' },
    });
    expect(insert).not.toHaveProperty('before_id');
    expect(insert).not.toHaveProperty('after_id');
    expect(allOps.find((op: ElementOp) => op.op === 'delete')).toMatchObject({
      op: 'delete',
      element_id: 'el_a',
    });
  });
});
