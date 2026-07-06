/**
 * Copilot free-text reconciler wiring (Phase B P2, Task 8).
 *
 * Drives SceneBlock's summoned card through the free-text flow with a stubbed
 * `requestCopilotOps` and a stateful mock `useSceneSync` (saveState stays
 * 'saved' so an applied turn settles to 'done'). Covers the five load-bearing
 * behaviors: request args, success dispatch + Undo, the proposal review branch
 * (Apply / Discard), the 422 failed state, and the 404 flag-off degrade.
 */
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ElementOp, ScriptElement, SceneDoc } from '../types';

// i18n: echo the key, appending interpolation values so counts are observable.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, opts?: Record<string, unknown>) =>
      opts ? `${k} ${Object.values(opts).join(' ')}` : k,
  }),
}));

// Stateful sync: records dispatched ops + applies the optimistic list. saveState
// stays 'saved' so an applied turn settles to 'done'.
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

// Stub only the network call; keep the real error classes + pure transforms.
const copilotApi = vi.hoisted(() => ({ requestCopilotOps: vi.fn() }));
vi.mock('../copilotService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../copilotService')>();
  return { ...actual, requestCopilotOps: copilotApi.requestCopilotOps };
});

import { CopilotDisabledError, OpRejectedError } from '../copilotService';
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

const typeInstruction = (value: string) =>
  fireEvent.change(screen.getByPlaceholderText('editor.copilotRequestPlaceholder'), {
    target: { value },
  });

const submit = () => fireEvent.click(screen.getByText('editor.copilotSend'));

afterEach(() => {
  cleanup();
  sync.dispatch.mockReset();
  copilotApi.requestCopilotOps.mockReset();
});

const oneScene = () => makeScene([{ id: 'el_a', type: 'action', text: 'Runs.' }]);

describe('SceneBlock copilot free-text', () => {
  it('submits with scene id, instruction, and the current scene version', async () => {
    copilotApi.requestCopilotOps.mockResolvedValueOnce({
      ops: [{ op: 'update', element_id: 'el_a', payload: { text: 'Sprints.' } }],
      base_version: 1,
      summary: 'Tightened.',
    });
    render(<SceneBlock scene={oneScene()} index={0} />);
    clickTick('el_a');
    typeInstruction('make it punchier');
    submit();

    await waitFor(() =>
      expect(copilotApi.requestCopilotOps).toHaveBeenCalledWith('900', 'make it punchier', 1),
    );
  });

  it('applies reconciled ops on success; Undo dispatches the inverse', async () => {
    copilotApi.requestCopilotOps.mockResolvedValueOnce({
      ops: [{ op: 'update', element_id: 'el_a', payload: { text: 'Sprints.' } }],
      base_version: 1,
      summary: 'Tightened the action.',
    });
    render(<SceneBlock scene={oneScene()} index={0} />);
    clickTick('el_a');
    typeInstruction('x');
    submit();

    // First dispatch = the reconciled ops.
    await waitFor(() => expect(sync.dispatch).toHaveBeenCalledTimes(1));
    const [ops] = sync.dispatch.mock.calls[0] as [ElementOp[]];
    expect(ops).toContainEqual({ op: 'update', element_id: 'el_a', payload: { text: 'Sprints.' } });

    // Done state shows the summary + edit count + Undo.
    const result = await screen.findByTestId('copilot-result');
    expect(result.textContent).toContain('editor.copilotEdits 1');
    expect(screen.getByTestId('copilot-summary').textContent).toBe('Tightened the action.');

    // Undo dispatches the inverse (an upsert carrying the ORIGINAL text).
    fireEvent.click(within(screen.getByTestId('copilot-card')).getByText('editor.copilotUndo'));
    expect(sync.dispatch).toHaveBeenCalledTimes(2);
    const [undoOps] = sync.dispatch.mock.calls[1] as [ElementOp[]];
    const restore = undoOps.find(
      (o): o is Extract<ElementOp, { op: 'insert' }> =>
        o.op === 'insert' && o.element_id === 'el_a',
    );
    expect(restore?.payload.text).toBe('Runs.');
  });

  it('parks a proposal without applying; Apply dispatches the ops', async () => {
    copilotApi.requestCopilotOps.mockResolvedValueOnce({
      proposal: true,
      ops: [{ op: 'update', element_id: 'el_a', payload: { text: 'Later.' } }],
      base_version: 5,
      summary: 'Reviewed against the current scene.',
    });
    render(<SceneBlock scene={oneScene()} index={0} />);
    clickTick('el_a');
    typeInstruction('rewrite it');
    submit();

    const proposal = await screen.findByTestId('copilot-proposal');
    expect(proposal).toBeInTheDocument();
    // Not auto-applied.
    expect(sync.dispatch).not.toHaveBeenCalled();

    fireEvent.click(within(proposal).getByText('editor.copilotApply'));
    expect(sync.dispatch).toHaveBeenCalledTimes(1);
    const [ops] = sync.dispatch.mock.calls[0] as [ElementOp[]];
    expect(ops).toContainEqual({ op: 'update', element_id: 'el_a', payload: { text: 'Later.' } });
  });

  it('Discard drops the proposal and returns the card to attached', async () => {
    copilotApi.requestCopilotOps.mockResolvedValueOnce({
      proposal: true,
      ops: [{ op: 'update', element_id: 'el_a', payload: { text: 'Later.' } }],
      base_version: 5,
      summary: 's',
    });
    render(<SceneBlock scene={oneScene()} index={0} />);
    clickTick('el_a');
    typeInstruction('rewrite it');
    submit();

    const proposal = await screen.findByTestId('copilot-proposal');
    fireEvent.click(within(proposal).getByText('editor.copilotDiscard'));

    await waitFor(() => expect(screen.queryByTestId('copilot-proposal')).toBeNull());
    expect(sync.dispatch).not.toHaveBeenCalled();
    expect(screen.getByTestId('copilot-card')).toHaveAttribute('data-phase', 'attached');
  });

  it('shows the op code in the failed state on 422 and dispatches nothing', async () => {
    copilotApi.requestCopilotOps.mockRejectedValueOnce(
      new OpRejectedError('missing_anchor', 'before_id el_x not found'),
    );
    render(<SceneBlock scene={oneScene()} index={0} />);
    clickTick('el_a');
    typeInstruction('do the thing');
    submit();

    const failed = await screen.findByTestId('copilot-failed');
    expect(failed.textContent).toContain('missing_anchor');
    expect(sync.dispatch).not.toHaveBeenCalled();
  });

  it('disables the free-text box on 404 and does not re-probe', async () => {
    copilotApi.requestCopilotOps.mockRejectedValueOnce(new CopilotDisabledError());
    render(<SceneBlock scene={oneScene()} index={0} />);
    clickTick('el_a');
    typeInstruction('anything');
    submit();

    await waitFor(() => expect(copilotApi.requestCopilotOps).toHaveBeenCalledTimes(1));
    // Box degrades to disabled with the unavailable placeholder.
    const disabledBox = await screen.findByPlaceholderText('editor.copilotDisabled');
    expect(disabledBox).toBeDisabled();

    // A further submit attempt must NOT hit the endpoint again.
    fireEvent.submit(disabledBox.closest('form') as HTMLFormElement);
    expect(copilotApi.requestCopilotOps).toHaveBeenCalledTimes(1);
  });
});
