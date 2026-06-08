import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { CreateBranchDialog } from './CreateBranchDialog';
import { createBranches } from '../../services/scriptService';

vi.mock('../../services/scriptService', () => ({ createBranches: vi.fn() }));
vi.mock('react-router-dom', () => ({ useParams: () => ({ scriptId: 's1' }) }));

const reloadScript = vi.fn().mockResolvedValue(undefined);
vi.mock('../../stores/scriptCanvasStore', () => ({
  useScriptCanvasStore: (sel: (s: { reloadScript: typeof reloadScript }) => unknown) =>
    sel({ reloadScript }),
}));

const addToast = vi.fn();
vi.mock('../../components/Toast', () => ({ useToast: () => ({ addToast }) }));

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

const mockCreate = vi.mocked(createBranches);

describe('CreateBranchDialog (async dispatch)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    lastTaskId = null;
    lastOpts = {};
    reloadScript.mockResolvedValue(undefined);
  });

  function renderDialog(onClose = vi.fn()) {
    render(
      <CreateBranchDialog
        isOpen
        onClose={onClose}
        chapterId="c1"
        title="Chapter One"
        summary="A fork in the road."
      />,
    );
    return onClose;
  }

  it('dispatches and shows generating state on submit', async () => {
    mockCreate.mockResolvedValue({ task_id: 'tk-br' });
    renderDialog();

    fireEvent.click(screen.getByText('Create 2 Branches'));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(lastTaskId).toBe('tk-br'));
    expect(await screen.findByText('Generating...')).toBeTruthy();
  });

  it('reloads the script and closes when the task completes', async () => {
    mockCreate.mockResolvedValue({ task_id: 'tk-br' });
    const onClose = renderDialog();

    fireEvent.click(screen.getByText('Create 2 Branches'));
    await waitFor(() => expect(lastTaskId).toBe('tk-br'));

    await act(async () => {
      await lastOpts.onComplete?.();
    });

    expect(reloadScript).toHaveBeenCalledWith('s1');
    expect(addToast).toHaveBeenCalledWith('Branches created', 'success');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows an error and re-enables when the task fails', async () => {
    mockCreate.mockResolvedValue({ task_id: 'tk-br' });
    const onClose = renderDialog();

    fireEvent.click(screen.getByText('Create 2 Branches'));
    await waitFor(() => expect(lastTaskId).toBe('tk-br'));

    act(() => {
      lastOpts.onError?.({ error_msg: 'branch boom' });
    });

    expect(addToast).toHaveBeenCalledWith('branch boom', 'error');
    expect(reloadScript).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(await screen.findByText('Create 2 Branches')).toBeTruthy();
  });
});
