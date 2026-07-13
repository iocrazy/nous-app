/**
 * M1 flag wiring (`VITE_FEATURE_SCRIPT_TIPTAP`, spec D7) — `SceneBlock`
 * branches ONLY its elements-area render on the flag:
 *  - off (default): the legacy LayoutEngine path, byte-identical to pre-M1
 *    (already covered exhaustively by SceneBlock.test.tsx; this file adds
 *    one explicit "not TipTap" assertion per the M1 task's regression bar).
 *  - on: `TipTapSceneEditor` mounts instead, and typing routes through the
 *    same `useSceneSync.dispatchOps` contract the legacy path uses.
 *
 * `isTiptapEnabled()` (`tiptap/flag.ts`) reads `import.meta.env` fresh on
 * every call rather than freezing it in a module-level const — so
 * `vi.stubEnv` + a normal render is enough here; no dynamic `import()` /
 * `vi.resetModules()` dance required (contrast `pages/inspirationFlag.test.tsx`,
 * which gates a module-level const and needs that dance).
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/react';
import { act } from '@testing-library/react';
import type { Editor } from '@tiptap/core';
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

// Same stateful useSceneSync mock as SceneBlock.test.tsx: dispatchOps
// records the call AND applies the optimistic list, so a TipTap-triggered
// dispatch is observable both as a call and as a re-render.
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

function getEditor(): Editor | undefined {
  return (window as unknown as Record<string, unknown>).__tipTapSceneEditorInstance as
    | Editor
    | undefined;
}

beforeEach(() => {
  (window as unknown as Record<string, unknown>).__tipTapSceneEditorInstance = undefined;
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.useRealTimers();
  sync.dispatch.mockClear();
  sync.reconcile.mockClear();
  sync.applyRemoteOps.mockClear();
  svc.updateSceneMeta.mockClear();
});

describe('flag off (default) — legacy LayoutEngine path', () => {
  it('renders the legacy row structure, not TipTapSceneEditor', () => {
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    expect(document.querySelector('.mh-tiptap-scene-editor-root')).toBeNull();
    expect(document.querySelector('[data-el-id="el_a"]')).not.toBeNull();
    expect(document.querySelector('[data-testid="empty-scene-hint"]')).toBeNull();
  });
});

describe('flag on — TipTapSceneEditor mounts and dispatches through useSceneSync', () => {
  it('mounts TipTapSceneEditor instead of the legacy LayoutEngine', async () => {
    vi.stubEnv('VITE_FEATURE_SCRIPT_TIPTAP', 'true');
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    await waitFor(() => expect(document.querySelector('.mh-tiptap-scene-editor-root')).not.toBeNull());
    expect(document.querySelector('[data-el-id="el_a"]')).not.toBeNull();
  });

  it('typing in TipTap mode dispatches ops through sync.dispatchOps (after the debounce)', async () => {
    vi.stubEnv('VITE_FEATURE_SCRIPT_TIPTAP', 'true');
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    await waitFor(() => expect(getEditor()).toBeTruthy());
    const editor = getEditor()!;

    vi.useFakeTimers();
    act(() => {
      editor.commands.insertContentAt(2, '!'); // end of "A" → "A!"
    });
    expect(sync.dispatch).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(500);
    });
    vi.useRealTimers();

    expect(sync.dispatch).toHaveBeenCalledTimes(1);
    const [ops] = sync.dispatch.mock.calls[0] as [ElementOp[]];
    expect(ops[0]).toMatchObject({ op: 'update', element_id: 'el_a', payload: { text: 'A!' } });
  });

  it('a structural edit (Tab retype) dispatches immediately, no debounce', async () => {
    vi.stubEnv('VITE_FEATURE_SCRIPT_TIPTAP', 'true');
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    await waitFor(() => expect(getEditor()).toBeTruthy());
    const editor = getEditor()!;

    act(() => {
      editor.commands.setTextSelection(2);
    });
    act(() => {
      editor.view.dom.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }),
      );
    });

    expect(sync.dispatch).toHaveBeenCalledTimes(1);
    const [ops] = sync.dispatch.mock.calls[0] as [ElementOp[]];
    expect(ops[0]).toMatchObject({ op: 'update', element_id: 'el_a', payload: { type: 'character' } });
  });

  it('an empty scene shows EmptySceneHint (PM schema requires ≥1 node); seeding mounts the editor', async () => {
    vi.stubEnv('VITE_FEATURE_SCRIPT_TIPTAP', 'true');
    render(<SceneBlock scene={makeScene([])} index={0} />);
    expect(document.querySelector('.mh-tiptap-scene-editor-root')).toBeNull();
    const hint = document.querySelector('[data-testid="empty-scene-hint"]');
    expect(hint).not.toBeNull();

    act(() => {
      (hint as HTMLElement).click();
    });
    expect(sync.dispatch).toHaveBeenCalledTimes(1);
    const [ops] = sync.dispatch.mock.calls[0] as [ElementOp[]];
    expect(ops[0]).toMatchObject({ op: 'insert', payload: { type: 'action', text: '' } });

    await waitFor(() => expect(document.querySelector('.mh-tiptap-scene-editor-root')).not.toBeNull());
  });
});
