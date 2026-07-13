/**
 * M2 remaining carry-overs, in TipTap mode (spec D6, M2 items 6/7):
 *  - EmptySceneHint's seed action focuses the freshly-seeded element.
 *  - The toolbar's TypeCommand retypes the focused element via the ref
 *    method `retypeElement` (immediate, caret-preserving) AND still
 *    dispatches the ops (data plane unchanged).
 */
import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, cleanup, waitFor, act } from '@testing-library/react';
import type { Editor } from '@tiptap/core';
import type { ElementOp, SceneDoc } from '../types';
import type { TypeCommand } from '../components/SceneBlock';

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

function getEditor(): Editor | undefined {
  return (window as unknown as Record<string, unknown>).__tipTapSceneEditorInstance as
    | Editor
    | undefined;
}

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  sync.dispatch.mockClear();
  sync.reconcile.mockClear();
  sync.applyRemoteOps.mockClear();
  svc.updateSceneMeta.mockClear();
  (window as unknown as Record<string, unknown>).__tipTapSceneEditorInstance = undefined;
});

describe('TipTap M2 — EmptySceneHint seed focus', () => {
  it('seeding an empty scene mounts the editor with the caret in the new element', async () => {
    vi.stubEnv('VITE_FEATURE_SCRIPT_TIPTAP', 'true');
    render(<SceneBlock scene={makeScene([])} index={0} />);
    const hint = document.querySelector('[data-testid="empty-scene-hint"]') as HTMLElement;
    expect(hint).not.toBeNull();

    act(() => {
      hint.click();
    });
    expect(sync.dispatch).toHaveBeenCalledTimes(1);
    const [ops] = sync.dispatch.mock.calls[0] as [ElementOp[]];
    const newId = (ops[0] as Extract<ElementOp, { op: 'insert' }>).element_id;

    await waitFor(() => expect(document.querySelector('.mh-tiptap-scene-editor-root')).not.toBeNull());
    await waitFor(() => {
      const editor = getEditor();
      expect(editor).toBeTruthy();
      const ctx = editor!.state.doc.firstChild;
      expect(ctx?.attrs.id).toBe(newId);
      expect(document.activeElement?.classList.contains('mh-tiptap-scene-editor')).toBe(true);
    });
  });
});

describe('TipTap M2 — TypeCommand (toolbar retype)', () => {
  function Harness({ typeCommand }: { typeCommand?: TypeCommand }) {
    return (
      <SceneBlock
        scene={makeScene([{ id: 'el_a', type: 'action', text: 'Hello' }])}
        index={0}
        typeCommand={typeCommand}
      />
    );
  }

  it('retypes the targeted element via retypeElement AND dispatches the op', async () => {
    vi.stubEnv('VITE_FEATURE_SCRIPT_TIPTAP', 'true');
    const { rerender } = render(<Harness />);
    await waitFor(() => expect(getEditor()).toBeTruthy());
    const editor = getEditor()!;
    expect(editor.state.doc.firstChild!.attrs.elType).toBe('action');

    rerender(
      <Harness
        typeCommand={{ sceneId: '900', elementId: 'el_a', type: 'transition', nonce: 1 }}
      />,
    );

    await waitFor(() => expect(editor.state.doc.firstChild!.attrs.elType).toBe('transition'));
    // Text is untouched by a plain TypeCommand retype (unlike a slash pick).
    expect(editor.state.doc.firstChild!.textContent).toBe('Hello');
    expect(sync.dispatch).toHaveBeenCalledTimes(1);
    const [ops] = sync.dispatch.mock.calls[0] as [ElementOp[]];
    expect(ops).toEqual([{ op: 'update', element_id: 'el_a', payload: { type: 'transition' } }]);
  });

  it('a second TypeCommand with a new nonce retypes again', async () => {
    vi.stubEnv('VITE_FEATURE_SCRIPT_TIPTAP', 'true');
    const { rerender } = render(<Harness />);
    await waitFor(() => expect(getEditor()).toBeTruthy());
    const editor = getEditor()!;

    rerender(
      <Harness typeCommand={{ sceneId: '900', elementId: 'el_a', type: 'dialogue', nonce: 1 }} />,
    );
    await waitFor(() => expect(editor.state.doc.firstChild!.attrs.elType).toBe('dialogue'));

    rerender(
      <Harness typeCommand={{ sceneId: '900', elementId: 'el_a', type: 'comment', nonce: 2 }} />,
    );
    await waitFor(() => expect(editor.state.doc.firstChild!.attrs.elType).toBe('comment'));
    expect(sync.dispatch).toHaveBeenCalledTimes(2);
  });

  it('a TypeCommand for a DIFFERENT scene id is ignored', async () => {
    vi.stubEnv('VITE_FEATURE_SCRIPT_TIPTAP', 'true');
    const { rerender } = render(<Harness />);
    await waitFor(() => expect(getEditor()).toBeTruthy());
    const editor = getEditor()!;

    rerender(
      <Harness typeCommand={{ sceneId: '999', elementId: 'el_a', type: 'dialogue', nonce: 1 }} />,
    );
    expect(sync.dispatch).not.toHaveBeenCalled();
    expect(editor.state.doc.firstChild!.attrs.elType).toBe('action');
  });
});
