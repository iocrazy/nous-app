import { renderHook } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useTaskCompletion } from './useTaskCompletion';
import type { UnifiedTask } from '../contexts/TaskManagerContext';

// Mock useTaskManager — the hook only reads `.tasks`.
const mockTasks = vi.fn<() => UnifiedTask[]>(() => []);
vi.mock('../contexts/TaskManagerContext', () => ({
  useTaskManager: () => ({ tasks: mockTasks() }),
}));

function makeTask(id: string, status: UnifiedTask['status'], extra: Partial<UnifiedTask> = {}): UnifiedTask {
  return {
    id,
    user_id: 'u1',
    task_type: 'agent',
    status,
    title: 'Test',
    progress: 0,
    metadata: {},
    created_at: '2026-06-09T00:00:00Z',
    ...extra,
  } as UnifiedTask;
}

describe('useTaskCompletion', () => {
  beforeEach(() => {
    mockTasks.mockReset();
    mockTasks.mockReturnValue([]);
  });

  it('does not fire while task is pending/processing', () => {
    const onComplete = vi.fn();
    const onError = vi.fn();
    mockTasks.mockReturnValue([makeTask('tk-1', 'processing')]);

    renderHook(() => useTaskCompletion('tk-1', { onComplete, onError }));

    expect(onComplete).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it('fires onComplete exactly once when the task flips to completed', () => {
    const onComplete = vi.fn();
    const onError = vi.fn();
    mockTasks.mockReturnValue([makeTask('tk-1', 'processing')]);

    const { rerender } = renderHook(() =>
      useTaskCompletion('tk-1', { onComplete, onError }),
    );
    expect(onComplete).not.toHaveBeenCalled();

    // Task completes.
    mockTasks.mockReturnValue([makeTask('tk-1', 'completed')]);
    rerender();
    expect(onComplete).toHaveBeenCalledTimes(1);

    // A later, harmless row update must not re-fire.
    mockTasks.mockReturnValue([makeTask('tk-1', 'completed', { progress: 100 })]);
    rerender();
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
  });

  it('fires onError exactly once when the task fails', () => {
    const onComplete = vi.fn();
    const onError = vi.fn();
    mockTasks.mockReturnValue([makeTask('tk-1', 'processing')]);

    const { rerender } = renderHook(() =>
      useTaskCompletion('tk-1', { onComplete, onError }),
    );

    mockTasks.mockReturnValue([makeTask('tk-1', 'failed', { error_msg: 'boom' })]);
    rerender();

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0].error_msg).toBe('boom');
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('fires onError on cancelled', () => {
    const onError = vi.fn();
    mockTasks.mockReturnValue([makeTask('tk-1', 'processing')]);
    const { rerender } = renderHook(() => useTaskCompletion('tk-1', { onError }));
    mockTasks.mockReturnValue([makeTask('tk-1', 'cancelled')]);
    rerender();
    expect(onError).toHaveBeenCalledTimes(1);
  });

  it('does nothing when taskId is null', () => {
    const onComplete = vi.fn();
    const onError = vi.fn();
    mockTasks.mockReturnValue([makeTask('tk-1', 'completed')]);
    renderHook(() => useTaskCompletion(null, { onComplete, onError }));
    expect(onComplete).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it('re-arms when taskId changes (second dispatch fires again)', () => {
    const onComplete = vi.fn();
    mockTasks.mockReturnValue([makeTask('tk-1', 'completed')]);

    const { rerender } = renderHook(
      ({ id }: { id: string }) => useTaskCompletion(id, { onComplete }),
      { initialProps: { id: 'tk-1' } },
    );
    expect(onComplete).toHaveBeenCalledTimes(1);

    // Watch a fresh task that is already completed.
    mockTasks.mockReturnValue([makeTask('tk-2', 'completed')]);
    rerender({ id: 'tk-2' });
    expect(onComplete).toHaveBeenCalledTimes(2);
  });

  it('returns status + task for generating UI', () => {
    mockTasks.mockReturnValue([makeTask('tk-1', 'processing')]);
    const { result } = renderHook(() => useTaskCompletion('tk-1', {}));
    expect(result.current.status).toBe('processing');
    expect(result.current.task?.id).toBe('tk-1');
  });
});
