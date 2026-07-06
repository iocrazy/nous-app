/**
 * ChapterActionsNode tests (Phase B Task 3).
 *
 * The node is rendered directly inside its action context (React Flow itself is
 * not needed — the node is a plain component). Covers: each action's inline
 * two-click confirm dispatching the right service, a single click auto-disarming
 * after the 3s window without dispatching, the busy state (aria-busy + disabled
 * actions), and the settle path (predicate match → onReload).
 */
import { render, screen, cleanup, fireEvent, waitFor, act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';
import { ReactFlowProvider } from '@xyflow/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const scriptSvc = vi.hoisted(() => ({
  expandChapter: vi.fn(),
  createBranches: vi.fn(),
}));
vi.mock('../../services/scriptService', () => scriptSvc);

const sceneSvc = vi.hoisted(() => ({
  convertToScenes: vi.fn(),
}));
vi.mock('../sceneService', () => sceneSvc);

import { ChapterActionsNode, ChapterActionContext } from '../nodes/ChapterActionsNode';
import type { ChapterActionContextValue } from '../nodes/ChapterActionsNode';
import type { ScriptChapter } from '../../types';

type NodeCompProps = ComponentProps<typeof ChapterActionsNode>;

function renderNode(ctxOverrides: Partial<ChapterActionContextValue> = {}) {
  const startPoll = vi.fn();
  const onReload = vi.fn();
  const ctx: ChapterActionContextValue = {
    scriptId: 's1',
    chapters: [],
    startPoll,
    onReload,
    ...ctxOverrides,
  };
  const props = {
    id: 'ch-100',
    data: { title: 'Act One', summary: 'A quiet room.', chapterNumber: 1 },
  } as unknown as NodeCompProps;
  const utils = render(
    <ReactFlowProvider>
      <ChapterActionContext.Provider value={ctx}>
        <ChapterActionsNode {...props} />
      </ChapterActionContext.Provider>
    </ReactFlowProvider>,
  );
  return { ...utils, startPoll, onReload };
}

/** First click arms "Confirm?", second click executes. */
function twoClick(labelKey: string) {
  fireEvent.click(screen.getByRole('button', { name: labelKey }));
  fireEvent.click(screen.getByRole('button', { name: 'editor.nodesConfirm' }));
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('ChapterActionsNode', () => {
  it('dispatches expandChapter after the inline two-click confirm', async () => {
    scriptSvc.expandChapter.mockResolvedValue({ task_id: 't1' });
    renderNode();

    twoClick('editor.nodesActionExpand');

    await waitFor(() =>
      expect(scriptSvc.expandChapter).toHaveBeenCalledWith({
        script_id: 's1',
        chapter_id: '100',
        title: 'Act One',
        summary: 'A quiet room.',
      }),
    );
  });

  it('dispatches createBranches (choice, 2) after two-click confirm', async () => {
    scriptSvc.createBranches.mockResolvedValue({ task_id: 't2' });
    renderNode();

    twoClick('editor.nodesActionBranch');

    await waitFor(() =>
      expect(scriptSvc.createBranches).toHaveBeenCalledWith({
        script_id: 's1',
        chapter_id: '100',
        title: 'Act One',
        summary: 'A quiet room.',
        branch_count: 2,
        branch_type: 'choice',
      }),
    );
  });

  it('dispatches convertToScenes after two-click confirm', async () => {
    sceneSvc.convertToScenes.mockResolvedValue('t3');
    renderNode();

    twoClick('editor.nodesActionConvert');

    await waitFor(() => expect(sceneSvc.convertToScenes).toHaveBeenCalledWith('s1', '100'));
  });

  it('auto-disarms after the confirm window without dispatching', () => {
    vi.useFakeTimers();
    try {
      renderNode();
      // Single click arms.
      fireEvent.click(screen.getByRole('button', { name: 'editor.nodesActionExpand' }));
      expect(screen.getByRole('button', { name: 'editor.nodesConfirm' })).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(3000));

      // Back to its label, nothing dispatched.
      expect(screen.getByRole('button', { name: 'editor.nodesActionExpand' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'editor.nodesConfirm' })).toBeNull();
      expect(scriptSvc.expandChapter).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('marks the node busy while dispatching, then clears + reloads on settle', async () => {
    scriptSvc.expandChapter.mockResolvedValue({ task_id: 't1' });
    const parentless: ScriptChapter[] = [];
    const { container, startPoll, onReload } = renderNode({ chapters: parentless });

    twoClick('editor.nodesActionExpand');

    const node = container.querySelector('.mh-flow-chapter') as HTMLElement;
    expect(node).toHaveAttribute('aria-busy', 'true');
    // Sibling actions are disabled while busy.
    expect(screen.getByRole('button', { name: 'editor.nodesActionBranch' })).toBeDisabled();

    await waitFor(() => expect(startPoll).toHaveBeenCalledTimes(1));

    // Expand's predicate is satisfied once a NEW child chapter appears.
    const [predicate, onSettled] = startPoll.mock.calls[0];
    expect(predicate({ scenes: [], chapters: [{ parent_chapter_id: '100' }] })).toBe(true);
    expect(predicate({ scenes: [], chapters: [] })).toBe(false);

    act(() => onSettled(true, { scenes: [], chapters: [] }));

    expect(onReload).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(node).not.toHaveAttribute('aria-busy'));
  });
});
