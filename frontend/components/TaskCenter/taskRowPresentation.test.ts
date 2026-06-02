import { describe, it, expect } from 'vitest';
import { taskRowActions, taskShowsCover } from './taskRowPresentation';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';

// Minimal task factory — only the fields the presentation helpers read.
function makeTask(overrides: Partial<UnifiedTask> = {}): UnifiedTask {
  return {
    id: 't1',
    user_id: 'u1',
    task_type: 'download',
    status: 'completed',
    title: 'Song A',
    progress: 100,
    metadata: {},
    created_at: '2026-06-02T00:00:00Z',
    ...overrides,
  } as UnifiedTask;
}

describe('taskShowsCover', () => {
  it('shows a cover for a completed task that produced a resource', () => {
    expect(taskShowsCover(makeTask({ status: 'completed', resource_id: 'r1' }))).toBe(true);
  });

  it('does not show a cover when there is no resource (e.g. failed parse)', () => {
    expect(taskShowsCover(makeTask({ status: 'failed', resource_id: undefined }))).toBe(false);
  });

  it('does not show a cover while the task is still running', () => {
    expect(taskShowsCover(makeTask({ status: 'processing', resource_id: 'r1' }))).toBe(false);
  });

  it('does not show a cover for a completed task with no resource_id', () => {
    expect(taskShowsCover(makeTask({ status: 'completed', resource_id: undefined }))).toBe(false);
  });
});

describe('taskRowActions', () => {
  it('a completed task with a resource can be opened and downloaded', () => {
    const a = taskRowActions(makeTask({ status: 'completed', resource_id: 'r1' }));
    expect(a).toEqual({ open: true, download: true, retry: false, cancel: false });
  });

  it('a completed task WITHOUT a resource exposes no open/download', () => {
    const a = taskRowActions(makeTask({ status: 'completed', resource_id: undefined }));
    expect(a).toEqual({ open: false, download: false, retry: false, cancel: false });
  });

  it('a running task can only be cancelled', () => {
    const a = taskRowActions(makeTask({ status: 'processing', resource_id: undefined }));
    expect(a).toEqual({ open: false, download: false, retry: false, cancel: true });
  });

  it('a pending task can only be cancelled', () => {
    const a = taskRowActions(makeTask({ status: 'pending' }));
    expect(a).toEqual({ open: false, download: false, retry: false, cancel: true });
  });

  it('a failed task can be retried', () => {
    const a = taskRowActions(makeTask({ status: 'failed', resource_id: undefined }));
    expect(a).toEqual({ open: false, download: false, retry: true, cancel: false });
  });

  it('a cancelled task can be retried', () => {
    const a = taskRowActions(makeTask({ status: 'cancelled' }));
    expect(a).toEqual({ open: false, download: false, retry: true, cancel: false });
  });

  it('a failed task that still produced a resource can be opened AND retried', () => {
    // Defensive: a partial-failure that left a resource behind should still be openable.
    const a = taskRowActions(makeTask({ status: 'failed', resource_id: 'r1' }));
    expect(a).toEqual({ open: true, download: true, retry: true, cancel: false });
  });
});
