import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ExpandChapterDialog } from './ExpandChapterDialog';
import { expandChapter } from '../../services/scriptService';

// ─── Mocks ───────────────────────────────────────────────
vi.mock('../../services/scriptService', () => ({ expandChapter: vi.fn() }));
vi.mock('react-router-dom', () => ({ useParams: () => ({ scriptId: 's1' }) }));

const reloadScript = vi.fn().mockResolvedValue(undefined);
vi.mock('../../stores/scriptCanvasStore', () => ({
  useScriptCanvasStore: (sel: (s: { reloadScript: typeof reloadScript }) => unknown) =>
    sel({ reloadScript }),
}));

const addToast = vi.fn();
vi.mock('../../components/Toast', () => ({ useToast: () => ({ addToast }) }));

// Capture the opts handed to useTaskCompletion so the test can simulate
// task completion / failure.
let lastTaskId: string | null = null;
let lastOpts: { onComplete?: () => void; onError?: (t: { error_msg?: string }) => void } = {};
vi.mock('../../hooks/useTaskCompletion', () => ({
  useTaskCompletion: (
    taskId: string | null,
    opts: { onComplete?: () => void; onError?: (t: { error_msg?: string }) => void },
  ) => {
    lastTaskId = taskId;
    lastOpts = opts;
    return {};
  },
}));

const mockExpand = vi.mocked(expandChapter);

describe('ExpandChapterDialog (async dispatch)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    lastTaskId = null;
    lastOpts = {};
    reloadScript.mockResolvedValue(undefined);
  });

  function renderDialog(onClose = vi.fn()) {
    render(
      <ExpandChapterDialog
        isOpen
        onClose={onClose}
        chapterId="c1"
        title="Chapter One"
        summary="A hero begins."
      />,
    );
    return onClose;
  }

  it('dispatches the service and enters generating state on submit', async () => {
    mockExpand.mockResolvedValue({ task_id: 'tk-exp' });
    renderDialog();

    fireEvent.click(screen.getByText('Expand with AI'));

    await waitFor(() => expect(mockExpand).toHaveBeenCalledTimes(1));
    expect(mockExpand).toHaveBeenCalledWith(
      expect.objectContaining({ script_id: 's1', chapter_id: 'c1' }),
    );
    await waitFor(() => expect(lastTaskId).toBe('tk-exp'));
    // Generating UI: button label flips + disables.
    expect(await screen.findByText('Expanding...')).toBeTruthy();
  });

  it('reloads the script and closes when the task completes', async () => {
    mockExpand.mockResolvedValue({ task_id: 'tk-exp' });
    const onClose = renderDialog();

    fireEvent.click(screen.getByText('Expand with AI'));
    await waitFor(() => expect(lastTaskId).toBe('tk-exp'));

    await act(async () => {
      await lastOpts.onComplete?.();
    });

    expect(reloadScript).toHaveBeenCalledWith('s1');
    expect(addToast).toHaveBeenCalledWith('Chapter expanded', 'success');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows an error and re-enables when the task fails', async () => {
    mockExpand.mockResolvedValue({ task_id: 'tk-exp' });
    const onClose = renderDialog();

    fireEvent.click(screen.getByText('Expand with AI'));
    await waitFor(() => expect(lastTaskId).toBe('tk-exp'));

    act(() => {
      lastOpts.onError?.({ error_msg: 'LLM exploded' });
    });

    expect(addToast).toHaveBeenCalledWith('LLM exploded', 'error');
    expect(reloadScript).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    // Re-enabled: default button label is back.
    expect(await screen.findByText('Expand with AI')).toBeTruthy();
    expect(screen.getByText('LLM exploded')).toBeTruthy();
  });
});
