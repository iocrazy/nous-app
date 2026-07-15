/**
 * M2 same-scene + cross-scene element drag, in TipTap mode (spec D6, M2
 * items 2/3/6). The NodeView's handle/row forward HTML5 DnD events to the
 * SAME `handleElementDragStart/Over/Drop/DragEnd` + `acceptExternalDrop`
 * callbacks `SceneBlock` already implements for the legacy layout engines
 * (see `TipTapSceneEditor.tsx`'s module doc) — so this suite mirrors
 * `elementReorder.test.tsx`'s harness/assertions almost verbatim, just with
 * the flag on and `.mh-el-row` sourced from the NodeView instead of
 * `ElementLine`.
 */
import { useRef, useState } from 'react';
import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, cleanup, waitFor, fireEvent, screen, within } from '@testing-library/react';
import type { ElementOp, SceneDoc } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const zeroRect = {
  bottom: 0,
  height: 0,
  left: 0,
  right: 0,
  toJSON: () => ({}),
  top: 0,
  width: 0,
  x: 0,
  y: 0,
};
Element.prototype.getClientRects = () => [] as unknown as DOMRectList;
Element.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
Range.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
if (typeof document.elementFromPoint !== 'function') {
  document.elementFromPoint = () => null;
}

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

const fakeDataTransfer = () => ({ setData: vi.fn(), getData: () => '', effectAllowed: '' });

const rowOf = (elementId: string): HTMLElement =>
  document.querySelector(`[data-el-id="${elementId}"]`)!.closest('.mh-el-row') as HTMLElement;

const stubRect = (el: HTMLElement) => {
  el.getBoundingClientRect = () =>
    ({ top: 0, bottom: 40, height: 40, left: 0, right: 0, width: 0, x: 0, y: 0, toJSON() {} }) as DOMRect;
};

const fireDragAt = (node: HTMLElement, type: 'dragover' | 'drop', clientY: number) => {
  const evt = new Event(type, { bubbles: true, cancelable: true });
  Object.assign(evt, { clientY, dataTransfer: fakeDataTransfer() });
  node.dispatchEvent(evt);
};

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
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

async function mountTiptap(elements: SceneDoc['elements']) {
  vi.stubEnv('VITE_FEATURE_SCRIPT_TIPTAP', 'true');
  render(<SceneBlock scene={makeScene(elements)} index={0} />);
  await waitFor(() => expect(document.querySelector('.mh-tiptap-scene-editor-root')).not.toBeNull());
}

describe('TipTap M2 — same-scene element drag', () => {
  it('renders the SAME row DOM (block number + 4-dot handle) legacy uses', async () => {
    await mountTiptap(threeElements());
    const nums = Array.from(document.querySelectorAll('.mh-el-num')).map((n) => n.textContent);
    expect(nums).toEqual(['2', '3', '4']);
    const handles = document.querySelectorAll('.mh-el-drag');
    expect(handles).toHaveLength(3);
    expect(handles[0].querySelectorAll('.mh-el-dot')).toHaveLength(4);
    expect(handles[0]).toHaveAttribute('draggable', 'true');
  });

  it('right-clicking a row opens a context menu that deletes THAT block', async () => {
    await mountTiptap(threeElements());
    fireEvent.contextMenu(rowOf('el_b'), { clientX: 10, clientY: 10 });
    const menu = screen.getByRole('menu');
    expect(within(menu).getByText('editor.ctxDeleteBlock')).toBeInTheDocument();

    fireEvent.click(within(menu).getByText('editor.ctxDeleteBlock'));
    expect(sync.dispatch).toHaveBeenCalledWith(
      [{ op: 'delete', element_id: 'el_b' }],
      expect.anything(),
    );
  });

  it('dragging C onto A\'s top edge dispatches EXACTLY ONE move op { before_id: A } (golden law)', async () => {
    await mountTiptap(threeElements());

    const cHandle = rowOf('el_c').querySelector('.mh-el-drag') as HTMLElement;
    const aRow = rowOf('el_a');
    stubRect(aRow);

    fireEvent.dragStart(cHandle, { dataTransfer: fakeDataTransfer() });
    fireDragAt(aRow, 'dragover', 5); // above midpoint → 'top'
    fireDragAt(aRow, 'drop', 5);

    expect(sync.dispatch).toHaveBeenCalledTimes(1);
    const [ops, optimistic] = sync.dispatch.mock.calls[0] as [ElementOp[], SceneDoc['elements']];
    expect(ops).toEqual([{ op: 'move', element_id: 'el_c', before_id: 'el_a' }]);
    expect(optimistic.map((e) => e.id)).toEqual(['el_c', 'el_a', 'el_b']);
  });

  it('dropping on the bottom edge dispatches move { after_id: target }', async () => {
    await mountTiptap(threeElements());

    const aHandle = rowOf('el_a').querySelector('.mh-el-drag') as HTMLElement;
    const bRow = rowOf('el_b');
    stubRect(bRow);

    fireEvent.dragStart(aHandle, { dataTransfer: fakeDataTransfer() });
    fireDragAt(bRow, 'dragover', 30); // below midpoint → 'bottom'
    fireDragAt(bRow, 'drop', 30);

    const [ops] = sync.dispatch.mock.calls[0] as [ElementOp[]];
    expect(ops).toEqual([{ op: 'move', element_id: 'el_a', after_id: 'el_b' }]);
  });

  it('dropping an element onto itself is a no-op (no dispatch)', async () => {
    await mountTiptap(threeElements());

    const bHandle = rowOf('el_b').querySelector('.mh-el-drag') as HTMLElement;
    const bRow = rowOf('el_b');
    stubRect(bRow);

    fireEvent.dragStart(bHandle, { dataTransfer: fakeDataTransfer() });
    fireDragAt(bRow, 'dragover', 5);
    fireDragAt(bRow, 'drop', 5);

    expect(sync.dispatch).not.toHaveBeenCalled();
  });

  it('the dragged row carries the .dragging class on its handle while in flight', async () => {
    await mountTiptap(threeElements());
    const aHandle = rowOf('el_a').querySelector('.mh-el-drag') as HTMLElement;
    fireEvent.dragStart(aHandle, { dataTransfer: fakeDataTransfer() });
    await waitFor(() => expect(aHandle).toHaveClass('dragging'));
  });
});

// ── Cross-scene paragraph drag: TWO tiptap-mode SceneBlocks, shell-wired ──
function TwoScenes({ sceneA, sceneB }: { sceneA: SceneDoc; sceneB: SceneDoc }) {
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

describe('TipTap M2 — cross-scene element drag', () => {
  const sceneA = (): SceneDoc => ({ ...makeScene(threeElements()), id: '900' });
  const sceneB = (elements: SceneDoc['elements']): SceneDoc => ({ ...makeScene(elements), id: '901' });

  async function mountTwoScenes(a: SceneDoc, b: SceneDoc) {
    vi.stubEnv('VITE_FEATURE_SCRIPT_TIPTAP', 'true');
    render(<TwoScenes sceneA={a} sceneB={b} />);
    await waitFor(() =>
      expect(document.querySelectorAll('.mh-tiptap-scene-editor-root').length).toBeGreaterThanOrEqual(1),
    );
  }

  it('dropping onto another (tiptap) scene inserts there then deletes from the source — insert BEFORE delete', async () => {
    await mountTwoScenes(sceneA(), sceneB([{ id: 'el_x', type: 'action', text: 'X-ray.' }]));

    const aHandle = rowOf('el_a').querySelector('.mh-el-drag') as HTMLElement;
    const xRow = rowOf('el_x');
    stubRect(xRow);

    fireEvent.dragStart(aHandle, { dataTransfer: fakeDataTransfer() });
    fireDragAt(xRow, 'dragover', 5); // above midpoint → before el_x
    fireDragAt(xRow, 'drop', 5);

    const allOps = sync.dispatch.mock.calls.map(([ops]) => (ops as ElementOp[])[0]);
    const insert = allOps.find((op) => op.op === 'insert');
    expect(insert).toMatchObject({
      op: 'insert',
      element_id: 'el_a',
      before_id: 'el_x',
      payload: { type: 'action', text: 'Alpha.' },
    });
    const del = allOps.find((op) => op.op === 'delete');
    expect(del).toMatchObject({ op: 'delete', element_id: 'el_a' });
    expect(allOps.indexOf(insert!)).toBeLessThan(allOps.indexOf(del!));
    expect(allOps.some((op) => op.op === 'move')).toBe(false);

    // M2 item 6: the dropped element is focused in ITS NEW (target) editor.
    // TipTap uses ONE contentEditable region per scene (not per-row, unlike
    // legacy) — "focused" here means DOM focus sits on the OWNING scene's
    // editor root (`.mh-tiptap-scene-editor`, `editorProps.attributes.class`)
    // AND that root is an ancestor of the dropped row (i.e. focus landed in
    // scene B, which now contains el_a, not scene A).
    await waitFor(() => {
      const active = document.activeElement;
      expect(active).not.toBeNull();
      expect(active?.classList.contains('mh-tiptap-scene-editor')).toBe(true);
      const node = document.querySelector('[data-el-id="el_a"]');
      expect(active?.contains(node)).toBe(true);
    });
  });

  it('dropping on an EMPTY (tiptap) scene\'s heading row appends the paragraph there', async () => {
    await mountTwoScenes(sceneA(), sceneB([]));

    const aHandle = rowOf('el_a').querySelector('.mh-el-drag') as HTMLElement;
    const headrows = document.querySelectorAll('.mh-scene-headrow');
    const bHead = headrows[1] as HTMLElement;

    fireEvent.dragStart(aHandle, { dataTransfer: fakeDataTransfer() });
    fireEvent.dragOver(bHead, { dataTransfer: fakeDataTransfer() });
    fireEvent.drop(bHead, { dataTransfer: fakeDataTransfer() });

    const allOps = sync.dispatch.mock.calls.map(([ops]) => (ops as ElementOp[])[0]);
    const insert = allOps.find((op) => op.op === 'insert');
    expect(insert).toMatchObject({ op: 'insert', element_id: 'el_a', payload: { type: 'action', text: 'Alpha.' } });
    expect(insert).not.toHaveProperty('before_id');
    expect(insert).not.toHaveProperty('after_id');
    expect(allOps.find((op) => op.op === 'delete')).toMatchObject({ op: 'delete', element_id: 'el_a' });

    // Target scene B was empty → mounts a fresh TipTapSceneEditor with the
    // dropped element as its sole node.
    await waitFor(() => expect(document.querySelector('[data-el-id="el_a"]')).not.toBeNull());
  });
});
