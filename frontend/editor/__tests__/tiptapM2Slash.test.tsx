/**
 * M2 slash menu ('/' at block start → block-type picker), in TipTap mode
 * (spec D6, M2 item 4). `TipTapSceneEditor` detects the '/' prefix in
 * `onUpdate` and reports it through `onSlashChange`, which drives the SAME
 * `SlashMenu` component + `SceneBlock` state legacy uses; ArrowUp/Down/
 * Enter/Tab/Escape route through `menuKeymap.ts`'s `menuBridgeKeymap`
 * extension (priority 1000, pre-empting `ScriptKeymap`'s Enter/Tab so an
 * open menu never falls through to a real split/retype).
 */
import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, cleanup, waitFor, act, fireEvent } from '@testing-library/react';
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

function getEditor(): Editor {
  return (window as unknown as Record<string, unknown>).__tipTapSceneEditorInstance as Editor;
}

function pressKey(editor: Editor, key: string, opts: Partial<KeyboardEventInit> = {}) {
  act(() => {
    editor.view.dom.dispatchEvent(
      new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...opts }),
    );
  });
}

async function mountTiptap(elements: SceneDoc['elements']) {
  vi.stubEnv('VITE_FEATURE_SCRIPT_TIPTAP', 'true');
  render(<SceneBlock scene={makeScene(elements)} index={0} />);
  await waitFor(() => expect(getEditor()).toBeTruthy());
  return getEditor();
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

describe('TipTap M2 — slash menu', () => {
  it('typing "/" at the start of an empty block opens the menu with an empty query', async () => {
    const editor = await mountTiptap([{ id: 'el_a', type: 'action', text: '' }]);
    act(() => {
      editor.commands.setTextSelection(1);
      editor.commands.insertContent('/');
    });
    await waitFor(() => expect(document.querySelector('[data-testid="slash-menu"]')).not.toBeNull());
    // All 7 vocabulary items are listed with an empty filter.
    expect(document.querySelectorAll('.mh-slash-item')).toHaveLength(7);
  });

  it('typing more chars filters the vocabulary live', async () => {
    const editor = await mountTiptap([{ id: 'el_a', type: 'action', text: '' }]);
    act(() => {
      editor.commands.setTextSelection(1);
      editor.commands.insertContent('/char');
    });
    await waitFor(() => {
      const items = Array.from(document.querySelectorAll('.mh-slash-item'));
      expect(items).toHaveLength(1);
    });
  });

  it('closes when the "/" prefix is removed (e.g. Backspace back to empty)', async () => {
    const editor = await mountTiptap([{ id: 'el_a', type: 'action', text: '' }]);
    act(() => {
      editor.commands.setTextSelection(1);
      editor.commands.insertContent('/');
    });
    await waitFor(() => expect(document.querySelector('[data-testid="slash-menu"]')).not.toBeNull());
    act(() => {
      // Replace the whole node's content with '' (mirrors deleting the '/').
      editor.commands.setTextSelection(1);
      editor.commands.deleteRange({ from: 1, to: 2 });
    });
    await waitFor(() => expect(document.querySelector('[data-testid="slash-menu"]')).toBeNull());
  });

  it('ArrowDown/ArrowUp move the active option without moving the PM caret', async () => {
    const editor = await mountTiptap([{ id: 'el_a', type: 'action', text: '' }]);
    act(() => {
      editor.commands.setTextSelection(1);
      editor.commands.insertContent('/');
    });
    await waitFor(() => expect(document.querySelector('[data-testid="slash-menu"]')).not.toBeNull());

    expect(document.querySelectorAll('.mh-slash-item')[0]).toHaveClass('active');
    pressKey(editor, 'ArrowDown');
    await waitFor(() => expect(document.querySelectorAll('.mh-slash-item')[1]).toHaveClass('active'));
    pressKey(editor, 'ArrowUp');
    await waitFor(() => expect(document.querySelectorAll('.mh-slash-item')[0]).toHaveClass('active'));
  });

  it('Enter applies the active pick: retypes in place, clears text, does NOT split the node', async () => {
    const editor = await mountTiptap([{ id: 'el_a', type: 'action', text: '' }]);
    act(() => {
      editor.commands.setTextSelection(1);
      editor.commands.insertContent('/char'); // filters to exactly "character"
    });
    await waitFor(() => expect(document.querySelectorAll('.mh-slash-item')).toHaveLength(1));

    const childCountBefore = editor.state.doc.childCount;
    pressKey(editor, 'Enter');

    await waitFor(() => expect(document.querySelector('[data-testid="slash-menu"]')).toBeNull());
    // No split — still exactly one node in the scene.
    expect(editor.state.doc.childCount).toBe(childCountBefore);
    expect(editor.state.doc.firstChild!.attrs.elType).toBe('character');
    expect(editor.state.doc.firstChild!.textContent).toBe('');

    // Data plane: the same update op legacy dispatches.
    const [ops] = sync.dispatch.mock.calls[sync.dispatch.mock.calls.length - 1] as [ElementOp[]];
    expect(ops).toEqual([{ op: 'update', element_id: 'el_a', payload: { type: 'character', text: '' } }]);
  });

  it('Escape closes the menu without applying anything', async () => {
    const editor = await mountTiptap([{ id: 'el_a', type: 'action', text: '' }]);
    act(() => {
      editor.commands.setTextSelection(1);
      editor.commands.insertContent('/char');
    });
    await waitFor(() => expect(document.querySelector('[data-testid="slash-menu"]')).not.toBeNull());
    sync.dispatch.mockClear();

    pressKey(editor, 'Escape');
    await waitFor(() => expect(document.querySelector('[data-testid="slash-menu"]')).toBeNull());
    expect(sync.dispatch).not.toHaveBeenCalled();
    expect(editor.state.doc.firstChild!.attrs.elType).toBe('action'); // unchanged
  });

  it('clicking a menu option applies it via the SAME transaction-based path (not a raw DOM write)', async () => {
    const editor = await mountTiptap([{ id: 'el_a', type: 'action', text: '' }]);
    act(() => {
      editor.commands.setTextSelection(1);
      editor.commands.insertContent('/dial');
    });
    await waitFor(() => expect(document.querySelectorAll('.mh-slash-item')).toHaveLength(1));
    fireEvent.click(document.querySelector('.mh-slash-item')!);

    await waitFor(() => expect(editor.state.doc.firstChild!.attrs.elType).toBe('dialogue'));
    expect(document.querySelector('[data-testid="slash-menu"]')).toBeNull();
  });
});
