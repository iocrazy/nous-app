import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { CreateStoryDialog } from './CreateStoryDialog';
import { generateOutline } from '../../services/scriptService';

vi.mock('../../services/scriptService', () => ({ generateOutline: vi.fn() }));
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

const mockOutline = vi.mocked(generateOutline);

describe('CreateStoryDialog (async outline — fixed flow)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    lastTaskId = null;
    lastOpts = {};
    reloadScript.mockResolvedValue(undefined);
  });

  function renderDialog(onClose = vi.fn()) {
    render(<CreateStoryDialog isOpen onClose={onClose} />);
    return onClose;
  }

  function typePremise() {
    fireEvent.change(screen.getByPlaceholderText(/Describe your story idea/i), {
      target: { value: 'A detective in a rainy city.' },
    });
  }

  it('dispatches generateOutline and shows generating state on submit', async () => {
    mockOutline.mockResolvedValue({ task_id: 'tk-out' });
    renderDialog();
    typePremise();

    fireEvent.click(screen.getByText('Generate Outline'));

    await waitFor(() => expect(mockOutline).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(lastTaskId).toBe('tk-out'));
    expect(await screen.findByText('Generating outline...')).toBeTruthy();
  });

  it('reloads the script and closes when the task completes', async () => {
    mockOutline.mockResolvedValue({ task_id: 'tk-out' });
    const onClose = renderDialog();
    typePremise();

    fireEvent.click(screen.getByText('Generate Outline'));
    await waitFor(() => expect(lastTaskId).toBe('tk-out'));

    await act(async () => {
      await lastOpts.onComplete?.();
    });

    expect(reloadScript).toHaveBeenCalledWith('s1');
    expect(addToast).toHaveBeenCalledWith('Story outline generated', 'success');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows an error and re-enables when the task fails', async () => {
    mockOutline.mockResolvedValue({ task_id: 'tk-out' });
    const onClose = renderDialog();
    typePremise();

    fireEvent.click(screen.getByText('Generate Outline'));
    await waitFor(() => expect(lastTaskId).toBe('tk-out'));

    act(() => {
      lastOpts.onError?.({ error_msg: 'outline boom' });
    });

    expect(addToast).toHaveBeenCalledWith('outline boom', 'error');
    expect(reloadScript).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    // Premise is preserved + button re-enabled.
    expect((screen.getByPlaceholderText(/Describe your story idea/i) as HTMLTextAreaElement).value)
      .toBe('A detective in a rainy city.');
    expect(await screen.findByText('Generate Outline')).toBeTruthy();
  });
});
