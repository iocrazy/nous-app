import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ElementOp, SceneDoc } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// Deterministic, unique element ids so anchored inserts are assertable. Also
// stubs updateSceneMeta so the head-row debounce test can observe the call.
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

// Stateful useSceneSync mock: dispatchOps records the ops AND applies the
// optimistic list so newly-inserted rows actually render (needed for focus).
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

afterEach(() => {
  cleanup();
  sync.dispatch.mockClear();
  sync.reconcile.mockClear();
  sync.applyRemoteOps.mockClear();
  svc.updateSceneMeta.mockClear();
  vi.useRealTimers();
});

// NOTE: the per-keystroke editing behaviors that used to live here — Tab type
// cycle, Enter split, paste-split, IME guard, and the 500ms text debounce —
// moved into the TipTap keymap/sync when the legacy contentEditable engine was
// retired, and are covered by `tiptapM1Keymap.test.tsx` / `tiptapM1Sync.test.tsx`
// / `tiptapM2Misc.test.tsx`. What remains here is the scene HEAD ROW (a plain
// React form, not the editing surface) and structural props.
describe('SceneBlock head row + structure', () => {
  it('writes scene meta through updateSceneMeta after the debounce', () => {
    vi.useFakeTimers();
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    // Head row starts as a typographic slug — click it to reveal the selects.
    fireEvent.click(screen.getByRole('button', { name: 'editor.editSceneHeading' }));
    const locationInput = screen.getByLabelText('editor.location');
    fireEvent.change(locationInput, { target: { value: 'Rooftop Access' } });

    expect(svc.updateSceneMeta).not.toHaveBeenCalled();
    vi.advanceTimersByTime(600);

    expect(svc.updateSceneMeta).toHaveBeenCalledWith(
      '900',
      expect.objectContaining({ location_text: 'Rooftop Access' }),
    );
  });
});

// The live-stats lift (structural change → immediate onElementsChange, text
// edits → 1s debounce) was previously exercised via a Tab keystroke on the
// legacy contentEditable. The lift EFFECT is engine-agnostic (it watches
// `sync.elements`), but its only trigger in this harness is a keystroke, which
// now flows through the TipTap keymap — so the retype→dispatch path is covered
// by `tiptapM1Keymap.test.tsx` and the immediate-vs-debounced lift by the
// toolbar-follow assertions in `EditorShell.test.tsx`.

describe('SceneBlock typographic head row (Task 4.5)', () => {
  it('renders a read-mode heading slug by default, not the selects', () => {
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    // Hollywood slug from INT / Blank Studio / NIGHT.
    expect(screen.getByRole('button', { name: 'editor.editSceneHeading' })).toHaveTextContent(
      'INT. BLANK STUDIO - NIGHT',
    );
    expect(screen.queryByLabelText('editor.location')).toBeNull();
    expect(screen.queryByLabelText('editor.intExt')).toBeNull();
  });

  it('reveals the three selects on click and returns to read mode on blur', () => {
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    fireEvent.click(screen.getByRole('button', { name: 'editor.editSceneHeading' }));

    expect(screen.getByLabelText('editor.intExt')).toBeInTheDocument();
    expect(screen.getByLabelText('editor.location')).toBeInTheDocument();
    expect(screen.getByLabelText('editor.timeOfDay')).toBeInTheDocument();

    // Blur to somewhere outside the head row → read mode returns.
    fireEvent.blur(screen.getByLabelText('editor.location'), { relatedTarget: document.body });
    expect(screen.queryByLabelText('editor.location')).toBeNull();
    expect(screen.getByRole('button', { name: 'editor.editSceneHeading' })).toBeInTheDocument();
  });

  it('reflects edited meta in the read-mode slug', () => {
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    fireEvent.click(screen.getByRole('button', { name: 'editor.editSceneHeading' }));
    fireEvent.change(screen.getByLabelText('editor.location'), {
      target: { value: 'Rooftop Access' },
    });
    fireEvent.blur(screen.getByLabelText('editor.location'), { relatedTarget: document.body });

    expect(screen.getByRole('button', { name: 'editor.editSceneHeading' })).toHaveTextContent(
      'INT. ROOFTOP ACCESS - NIGHT',
    );
  });

  // ── Windowed-out remote-op refetch (C2 / M1) ────────────────────────────────
  describe('remoteStale refetch on remount', () => {
    it('refetches once and clears the flag when remounted with a dropped op', () => {
      const onHandled = vi.fn();
      render(
        <SceneBlock
          scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])}
          index={0}
          remoteStale
          onRemoteStaleHandled={onHandled}
        />,
      );
      expect(sync.reconcile).toHaveBeenCalledTimes(1);
      expect(onHandled).toHaveBeenCalledWith('900');
    });

    it('does not refetch a scene that had no dropped op', () => {
      render(
        <SceneBlock
          scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])}
          index={0}
          remoteStale={false}
          onRemoteStaleHandled={vi.fn()}
        />,
      );
      expect(sync.reconcile).not.toHaveBeenCalled();
    });
  });

  // ── #1006: scene ids are JSON numbers at runtime, keys must be String()'d ────
  describe('scene-id coercion at collab boundaries (number id)', () => {
    // The scenes API returns id as a JSON number despite the string type; build
    // a scene whose id is a real number to exercise the Map-key coercion.
    const numScene = (): SceneDoc => ({
      ...makeScene([{ id: 'el_a', type: 'action', text: 'A' }]),
      id: 900 as unknown as string,
    });

    it('registers applyRemoteOps under a STRING key even when scene.id is a number', () => {
      const onRegister = vi.fn();
      render(<SceneBlock scene={numScene()} index={0} onRegisterRemoteApply={onRegister} />);
      expect(onRegister).toHaveBeenCalledWith('900', sync.applyRemoteOps);
      // Key must be the string '900', never the number 900.
      expect(onRegister.mock.calls[0][0]).toBe('900');
    });

    it('clears the dropped flag with a STRING key when scene.id is a number', () => {
      const onHandled = vi.fn();
      render(
        <SceneBlock
          scene={numScene()}
          index={0}
          remoteStale
          onRemoteStaleHandled={onHandled}
        />,
      );
      expect(onHandled).toHaveBeenCalledWith('900');
      expect(onHandled.mock.calls[0][0]).toBe('900');
    });
  });

  // ── A1: continuous per-block numbering (badge shows the document-order
  // block number, not the scene's position) ────────────────────────────────
  describe('continuous block numbering (blockIndexBase)', () => {
    // The element rows mount asynchronously (TipTap builds its ProseMirror view
    // in an effect), so wait for the NodeView `.mh-el-num` before asserting.
    it('defaults the scene heading badge to 1 when blockIndexBase is omitted', async () => {
      render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
      expect(document.querySelector('.mh-scene-num-badge')?.textContent).toBe('1');
      await waitFor(() => {
        const num = document
          .querySelector('[data-el-id="el_a"]')
          ?.closest('.mh-el-row')
          ?.querySelector('.mh-el-num');
        expect(num?.textContent).toBe('2');
      });
    });

    it('offsets the heading badge and element numbers by blockIndexBase', async () => {
      render(
        <SceneBlock
          scene={makeScene([
            { id: 'el_a', type: 'action', text: 'A' },
            { id: 'el_b', type: 'action', text: 'B' },
          ])}
          index={1}
          blockIndexBase={3}
        />,
      );
      expect(document.querySelector('.mh-scene-num-badge')?.textContent).toBe('4');
      await waitFor(() => {
        const numA = document
          .querySelector('[data-el-id="el_a"]')
          ?.closest('.mh-el-row')
          ?.querySelector('.mh-el-num');
        expect(numA?.textContent).toBe('5');
      });
      const numB = document
        .querySelector('[data-el-id="el_b"]')
        ?.closest('.mh-el-row')
        ?.querySelector('.mh-el-num');
      expect(numB?.textContent).toBe('6');
    });
  });
});
