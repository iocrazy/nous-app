/**
 * M2 mentions + character-cue picker, in TipTap mode (spec D6, M2 item 5).
 * Two trigger paths:
 *  - inline: typing `@word` anywhere in a non-character line opens/updates
 *    the SAME `MentionCombobox` legacy uses (query = the run after `@` under
 *    the caret — `detectInlineMentionQuery` in `TipTapSceneEditor.tsx`).
 *  - character-cue: the picker is INVITED, not sprung. It opens on a real click
 *    on the cue NAME, or on any caret in an EMPTY cue line (nothing to offer
 *    there but the cast). A caret arriving by keyboard/typing/focusElement()
 *    only refreshes the query of an ALREADY-open picker — see
 *    `onSelectionUpdate` in `TipTapSceneEditor.tsx`.
 * Selection replaces text via a transaction (`replaceElementText`), not a
 * raw DOM write — M2 renders mentions as PLAIN TEXT (no chips; chips are a
 * documented M3/M4 follow-up, see the M2 report).
 *
 * NOTE: the click-on-the-name trigger cannot be exercised here — jsdom has no
 * layout and this file stubs `Range.getClientRects()` to [], so the hit test
 * that distinguishes the name from the blank space after it always misses.
 * That path is covered in `e2e/script-editor-cue-picker.spec.ts`, which runs
 * against real geometry. These tests open the picker the other legitimate way
 * (an empty cue), which needs no geometry.
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

async function mountTiptap(elements: SceneDoc['elements'], mentionCandidates: string[] = []) {
  vi.stubEnv('VITE_FEATURE_SCRIPT_TIPTAP', 'true');
  render(<SceneBlock scene={makeScene(elements)} index={0} mentionCandidates={mentionCandidates} />);
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

describe('TipTap M2 — inline @mention', () => {
  it('typing "@" on an action line opens the combobox with an empty query', async () => {
    const editor = await mountTiptap([{ id: 'el_a', type: 'action', text: '' }], ['BOB', 'ALICE']);
    act(() => {
      editor.commands.setTextSelection(1);
      editor.commands.insertContent('@');
    });
    await waitFor(() => expect(document.querySelector('[data-testid="mention-combobox"]')).not.toBeNull());
    expect(document.querySelectorAll('.mh-mention-opt')).toHaveLength(2);
  });

  it('continuing to type filters the candidate list live', async () => {
    const editor = await mountTiptap([{ id: 'el_a', type: 'action', text: '' }], ['BOB', 'ALICE']);
    act(() => {
      editor.commands.setTextSelection(1);
      editor.commands.insertContent('@bo');
    });
    await waitFor(() => {
      const opts = Array.from(document.querySelectorAll('.mh-mention-opt'));
      expect(opts.map((o) => o.textContent)).toEqual(['BOB']);
    });
  });

  it('selecting a candidate replaces the @run with "@Name " via a transaction (no chip, plain text)', async () => {
    const editor = await mountTiptap([{ id: 'el_a', type: 'action', text: 'Hi ' }], ['BOB']);
    act(() => {
      editor.commands.setTextSelection(4); // caret after "Hi "
      editor.commands.insertContent('@b');
    });
    await waitFor(() => expect(document.querySelectorAll('.mh-mention-opt')).toHaveLength(1));

    pressKey(editor, 'Enter');

    await waitFor(() => expect(document.querySelector('[data-testid="mention-combobox"]')).toBeNull());
    expect(editor.state.doc.firstChild!.textContent).toBe('Hi @BOB ');
    // Rendered as plain text — a real PM text node, no mark/chip wrapper.
    expect(editor.state.doc.firstChild!.childCount).toBe(1);
    expect(editor.state.doc.firstChild!.firstChild!.isText).toBe(true);

    const [ops] = sync.dispatch.mock.calls[sync.dispatch.mock.calls.length - 1] as [ElementOp[]];
    expect(ops).toEqual([{ op: 'update', element_id: 'el_a', payload: { text: 'Hi @BOB ' } }]);
  });

  it('closes when the @run is broken (a space typed after it)', async () => {
    const editor = await mountTiptap([{ id: 'el_a', type: 'action', text: '' }], ['BOB']);
    act(() => {
      editor.commands.setTextSelection(1);
      editor.commands.insertContent('@bob');
    });
    await waitFor(() => expect(document.querySelector('[data-testid="mention-combobox"]')).not.toBeNull());
    act(() => {
      editor.commands.insertContent(' ');
    });
    await waitFor(() => expect(document.querySelector('[data-testid="mention-combobox"]')).toBeNull());
  });

  it('Escape closes the combobox without changing the text', async () => {
    const editor = await mountTiptap([{ id: 'el_a', type: 'action', text: '' }], ['BOB']);
    act(() => {
      editor.commands.setTextSelection(1);
      editor.commands.insertContent('@bob');
    });
    await waitFor(() => expect(document.querySelector('[data-testid="mention-combobox"]')).not.toBeNull());
    pressKey(editor, 'Escape');
    await waitFor(() => expect(document.querySelector('[data-testid="mention-combobox"]')).toBeNull());
    expect(editor.state.doc.firstChild!.textContent).toBe('@bob');
  });
});

/**
 * Park the caret at the far end of the doc, then move it to `pos`.
 *
 * A bare `setTextSelection(pos)` can land on the selection the editor already
 * has, and ProseMirror fires no selection update for a move that isn't one — the
 * picker would then never be invited and the test would fail for a reason that
 * has nothing to do with the contract under test. The round trip guarantees a
 * real transition into `pos`.
 */
function caretInto(editor: Editor, pos: number): void {
  act(() => {
    editor.commands.setTextSelection(editor.state.doc.content.size - 1);
  });
  act(() => {
    editor.commands.setTextSelection(pos);
  });
}

describe('TipTap M2 — character-cue picker', () => {
  it('a caret arriving without a click does NOT open the cue picker', async () => {
    const editor = await mountTiptap(
      [
        { id: 'el_a', type: 'action', text: 'Intro.' },
        { id: 'el_c', type: 'character', text: 'BO' },
      ],
      ['BOB', 'ALICE'],
    );
    act(() => {
      editor.commands.setTextSelection(editor.state.doc.content.size - 1); // inside el_c
    });
    // Give it the same beat the old contract needed to render the popup.
    await new Promise((r) => setTimeout(r, 50));
    expect(document.querySelector('[data-testid="mention-combobox"]')).toBeNull();
  });

  it('an empty cue line opens the picker, and typing then filters it', async () => {
    const editor = await mountTiptap(
      [
        { id: 'el_c', type: 'character', text: '' },
        { id: 'el_d', type: 'dialogue', text: 'Hello.' },
      ],
      ['BOB', 'ALICE'],
    );
    caretInto(editor, 1);
    // Empty cue → the whole cast, unfiltered.
    await waitFor(() => {
      const opts = Array.from(document.querySelectorAll('.mh-mention-opt'));
      expect(opts.map((o) => o.textContent)).toEqual(['BOB', 'ALICE']);
    });

    act(() => {
      editor.commands.insertContent('BO');
    });
    // Typing rides the query bridge — the invited picker narrows, never reopens.
    // "BO" is a substring of BOB but not an exact hit, so the search-or-create
    // list also carries the "Create <query>" row (location-field parity).
    await waitFor(() => {
      const opts = Array.from(document.querySelectorAll('.mh-mention-opt'));
      expect(opts.map((o) => o.textContent)).toEqual(['BOB', 'editor.cueCreate']);
    });
  });

  it('Tab abandons the cue: retypes the line to action and closes (does not apply a candidate)', async () => {
    const editor = await mountTiptap(
      [
        { id: 'el_c', type: 'character', text: '' },
        { id: 'el_d', type: 'dialogue', text: 'Hello.' },
      ],
      ['BOB'],
    );
    caretInto(editor, 1);
    await waitFor(() => expect(document.querySelector('[data-testid="mention-combobox"]')).not.toBeNull());
    act(() => {
      editor.commands.insertContent('BO');
    });

    pressKey(editor, 'Tab');

    await waitFor(() => expect(document.querySelector('[data-testid="mention-combobox"]')).toBeNull());
    expect(editor.state.doc.firstChild!.attrs.elType).toBe('action');
    const [ops] = sync.dispatch.mock.calls[sync.dispatch.mock.calls.length - 1] as [ElementOp[]];
    expect(ops).toEqual([{ op: 'update', element_id: 'el_c', payload: { type: 'action' } }]);
  });

  it('Enter applies the active candidate: the WHOLE line becomes the name', async () => {
    const editor = await mountTiptap(
      [
        { id: 'el_c', type: 'character', text: '' },
        { id: 'el_d', type: 'dialogue', text: 'Hello.' },
      ],
      ['BOB'],
    );
    caretInto(editor, 1);
    await waitFor(() => expect(document.querySelector('[data-testid="mention-combobox"]')).not.toBeNull());
    act(() => {
      editor.commands.insertContent('BO');
    });
    // BOB (the substring match) plus the appended "Create BO" row = two options;
    // the active row is still index 0 (BOB), so Enter commits the candidate.
    await waitFor(() => expect(document.querySelectorAll('.mh-mention-opt')).toHaveLength(2));

    pressKey(editor, 'Enter');

    await waitFor(() => expect(editor.state.doc.firstChild!.textContent).toBe('BOB'));
    expect(editor.state.doc.firstChild!.attrs.elType).toBe('character'); // Enter doesn't retype
  });

  it('moving focus to a different (non-character) line closes the picker', async () => {
    const editor = await mountTiptap(
      [
        { id: 'el_c', type: 'character', text: '' },
        { id: 'el_d', type: 'dialogue', text: 'Hello.' },
      ],
      ['BOB'],
    );
    caretInto(editor, 1); // inside the empty cue → invited
    await waitFor(() => expect(document.querySelector('[data-testid="mention-combobox"]')).not.toBeNull());

    act(() => {
      editor.commands.setTextSelection(editor.state.doc.content.size - 1); // inside el_d
    });
    await waitFor(() => expect(document.querySelector('[data-testid="mention-combobox"]')).toBeNull());
  });
});
