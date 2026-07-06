import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ElementOp, ScriptElement, SceneDoc } from '../types';

// i18n: echo the key AND append option values so count/scene interpolation is
// observable in assertions (the real strings are {{scene}}/{{count}} templates).
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, opts?: Record<string, unknown>) =>
      opts ? `${k} ${Object.values(opts).join(' ')}` : k,
  }),
}));

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

// Stateful sync: records dispatched ops + applies the optimistic list so the
// selection/undo flows operate on a live element view. saveState stays 'saved'
// so the phase settles to 'done' after a polish.
const sync = vi.hoisted(() => ({ dispatch: vi.fn() }));
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
        dispatchOps: (ops: ElementOp[], optimistic: ScriptElement[]) => {
          sync.dispatch(ops, optimistic);
          setElements(optimistic);
        },
        resolveConflict: () => {},
        flush: async () => {},
      };
    },
  };
});

import { buildPolishOps, polishText } from '../copilotService';
import { CopilotCard } from '../components/CopilotCard';
import { SceneBlock } from '../components/SceneBlock';

const makeScene = (elements: ScriptElement[]): SceneDoc => ({
  id: '900',
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Studio',
  time_of_day: 'NIGHT',
  content_version: 1,
  sort_order: 0,
  elements,
});

const clickTick = (id: string, shiftKey = false) =>
  fireEvent.click(document.querySelector(`[data-tick-id="${id}"]`) as HTMLElement, { shiftKey });

afterEach(() => {
  cleanup();
  sync.dispatch.mockReset();
});

// ─── Pure copilot transforms ─────────────────────────────────────────────────

describe('copilotService', () => {
  it('polishText trims + collapses whitespace, upcasing only character cues', () => {
    expect(polishText('  padded  ', 'action')).toBe('padded');
    expect(polishText('double  space', 'action')).toBe('double space');
    expect(polishText('client', 'character')).toBe('CLIENT');
    expect(polishText('already clean', 'action')).toBe('already clean');
  });

  it('buildPolishOps emits ops ONLY for elements whose text changes', () => {
    const els: ScriptElement[] = [
      { id: 'a', type: 'action', text: '  padded  ' },
      { id: 'b', type: 'character', text: 'client' },
      { id: 'c', type: 'action', text: 'clean' },
    ];
    const ops = buildPolishOps(els, ['a', 'b', 'c']);
    expect(ops).toHaveLength(2); // c is unchanged → no op
    expect(ops).toContainEqual({ op: 'update', element_id: 'a', payload: { text: 'padded' } });
    expect(ops).toContainEqual({ op: 'update', element_id: 'b', payload: { text: 'CLIENT' } });
  });

  it('buildPolishOps ignores unselected elements', () => {
    const els: ScriptElement[] = [{ id: 'a', type: 'action', text: '  x  ' }];
    expect(buildPolishOps(els, [])).toEqual([]);
  });
});

// ─── CopilotCard presentational ──────────────────────────────────────────────

describe('CopilotCard', () => {
  it('keeps Summarize a disabled placeholder but unlocks the free-text box', () => {
    render(
      <CopilotCard
        sceneNumber={1}
        selectedCount={2}
        phase="attached"
        editsThisTurn={null}
        canUndo={false}
        onPolish={vi.fn()}
        onUndo={vi.fn()}
        instruction=""
        onInstructionChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByText('editor.copilotSummarize')).toBeDisabled();
    // Free-text is now live (Task 8): the box is enabled with the request
    // placeholder; Send is disabled only until the writer types something.
    expect(screen.getByPlaceholderText('editor.copilotRequestPlaceholder')).toBeEnabled();
    expect(screen.getByText('editor.copilotSend')).toBeDisabled();
    // Polish stays live.
    expect(screen.getByText('editor.copilotPolish')).toBeEnabled();
  });

  it('shows the edit count and Undo in the done phase', () => {
    render(
      <CopilotCard
        sceneNumber={1}
        selectedCount={2}
        phase="done"
        editsThisTurn={2}
        canUndo
        onPolish={vi.fn()}
        onUndo={vi.fn()}
      />,
    );
    const result = screen.getByTestId('copilot-result');
    expect(result.textContent).toContain('2'); // "{{count}} edits this turn"
    expect(screen.getByText('editor.copilotUndo')).toBeInTheDocument();
  });
});

// ─── SceneBlock summon + apply integration ───────────────────────────────────

describe('SceneBlock copilot integration', () => {
  it('renders no card until an element is selected', () => {
    render(<SceneBlock scene={makeScene([{ id: 'el_a', type: 'action', text: 'A' }])} index={0} />);
    expect(screen.queryByTestId('copilot-card')).toBeNull();

    clickTick('el_a');
    expect(screen.getByTestId('copilot-card')).toBeInTheDocument();
  });

  it('summons the card titled with the scene number and selected count (shift-range)', () => {
    render(
      <SceneBlock
        scene={makeScene([
          { id: 'el_a', type: 'action', text: 'A' },
          { id: 'el_b', type: 'action', text: 'B' },
        ])}
        index={0}
      />,
    );
    clickTick('el_a');
    clickTick('el_b', true); // shift-click extends the range → 2 elements

    const target = screen.getByTestId('copilot-target');
    expect(target.textContent).toContain('1'); // Scene 1
    expect(target.textContent).toContain('2'); // 2 elements
  });

  it('Polish dispatches update ops only for changed elements', () => {
    render(
      <SceneBlock
        scene={makeScene([
          { id: 'el_a', type: 'action', text: '  padded  ' },
          { id: 'el_b', type: 'action', text: 'double  space' },
          { id: 'el_c', type: 'character', text: 'client' },
          { id: 'el_d', type: 'action', text: 'clean' },
        ])}
        index={0}
      />,
    );
    clickTick('el_a');
    clickTick('el_d', true); // range el_a..el_d = all four selected

    fireEvent.click(screen.getByText('editor.copilotPolish'));

    const [ops] = sync.dispatch.mock.calls[0] as [ElementOp[]];
    expect(ops).toHaveLength(3); // el_d is already clean → skipped
    expect(ops).toContainEqual({ op: 'update', element_id: 'el_a', payload: { text: 'padded' } });
    expect(ops).toContainEqual({
      op: 'update',
      element_id: 'el_b',
      payload: { text: 'double space' },
    });
    expect(ops).toContainEqual({ op: 'update', element_id: 'el_c', payload: { text: 'CLIENT' } });
  });

  it('shows the applied-edit count after Polish', () => {
    render(
      <SceneBlock
        scene={makeScene([
          { id: 'el_a', type: 'action', text: '  padded  ' },
          { id: 'el_b', type: 'character', text: 'client' },
        ])}
        index={0}
      />,
    );
    clickTick('el_a');
    clickTick('el_b', true);
    fireEvent.click(screen.getByText('editor.copilotPolish'));

    const result = screen.getByTestId('copilot-result');
    expect(result.textContent).toContain('2'); // 2 edits this turn
  });

  it('Undo dispatches the inverse batch, restoring original text', () => {
    render(
      <SceneBlock
        scene={makeScene([{ id: 'el_c', type: 'character', text: 'client' }])}
        index={0}
      />,
    );
    clickTick('el_c');
    fireEvent.click(screen.getByText('editor.copilotPolish'));

    // Second dispatch is the Undo (inverse of the polish).
    const card = screen.getByTestId('copilot-card');
    fireEvent.click(within(card).getByText('editor.copilotUndo'));

    const undoCall = sync.dispatch.mock.calls[1] as [ElementOp[]];
    const [undoOps] = undoCall;
    const restore = undoOps.find(
      (o): o is Extract<ElementOp, { op: 'insert' }> =>
        o.op === 'insert' && o.element_id === 'el_c',
    );
    expect(restore?.payload.text).toBe('client');
  });
});
