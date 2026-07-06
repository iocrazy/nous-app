/**
 * VersionPanel tests (Phase B P4).
 *
 * Covers: loading + rendering the commit list, the inline Save Version flow
 * (create + reload), two-click rollback (API + reload callback), a partial
 * rollback surfacing per-scene error codes inline, and two-click delete.
 */
import { render, screen, cleanup, fireEvent, waitFor, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RollbackResult, ScriptCommit } from '../sceneService';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const svc = vi.hoisted(() => ({
  listCommits: vi.fn(),
  createCommit: vi.fn(),
  rollbackCommit: vi.fn(),
  deleteCommit: vi.fn(),
}));
vi.mock('../sceneService', () => svc);

const addToast = vi.fn();
vi.mock('../../components/Toast', () => ({
  useToast: () => ({ addToast }),
}));

import { VersionPanel } from '../versions/VersionPanel';

const commit = (over: Partial<ScriptCommit> = {}): ScriptCommit => ({
  id: '500',
  script_id: '1',
  message: 'First draft',
  watermarks: { '200': 3 },
  scene_ids: [],
  created_by: 'user-1',
  created_at: new Date().toISOString(),
  ...over,
});

const rollback = (over: Partial<RollbackResult> = {}): RollbackResult => ({
  commit_id: '500',
  results: [{ scene_id: '200', status: 'rolled_back' }],
  not_deleted: [],
  not_resurrected: [],
  partial_failure: false,
  ...over,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('VersionPanel', () => {
  beforeEach(() => {
    svc.listCommits.mockResolvedValue([]);
  });

  it('renders the commit list newest-first', async () => {
    svc.listCommits.mockResolvedValue([
      commit({ id: '501', message: 'Second pass' }),
      commit({ id: '500', message: 'First draft' }),
    ]);
    render(<VersionPanel scriptId="1" onCompare={vi.fn()} onRolledBack={vi.fn()} />);

    await waitFor(() => expect(screen.getAllByTestId('version-item')).toHaveLength(2));
    expect(screen.getByText('Second pass')).toBeInTheDocument();
    expect(screen.getByText('First draft')).toBeInTheDocument();
  });

  it('saves a version through the inline input and reloads the list', async () => {
    svc.createCommit.mockResolvedValue(commit({ id: '502', message: 'Checkpoint' }));
    render(<VersionPanel scriptId="1" onCompare={vi.fn()} onRolledBack={vi.fn()} />);
    await waitFor(() => expect(svc.listCommits).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByText('editor.saveVersion'));
    fireEvent.change(screen.getByLabelText('editor.versionMessagePlaceholder'), {
      target: { value: 'Checkpoint' },
    });
    await act(async () => {
      fireEvent.click(screen.getByText('editor.versionSaveConfirm'));
    });

    expect(svc.createCommit).toHaveBeenCalledWith('1', 'Checkpoint');
    // Mount load + post-save reload.
    await waitFor(() => expect(svc.listCommits).toHaveBeenCalledTimes(2));
  });

  it('rolls back on the second (confirming) click and reloads the editor', async () => {
    svc.listCommits.mockResolvedValue([commit({ id: '500' })]);
    svc.rollbackCommit.mockResolvedValue(rollback());
    const onRolledBack = vi.fn();
    render(<VersionPanel scriptId="1" onCompare={vi.fn()} onRolledBack={onRolledBack} />);
    await waitFor(() => expect(screen.getByTestId('version-item')).toBeInTheDocument());

    const btn = screen.getByText('editor.versionRollback');
    fireEvent.click(btn); // arm
    expect(svc.rollbackCommit).not.toHaveBeenCalled();
    expect(screen.getByText('editor.versionRollbackConfirm')).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByText('editor.versionRollbackConfirm')); // confirm
    });
    expect(svc.rollbackCommit).toHaveBeenCalledWith('1', '500');
    expect(onRolledBack).toHaveBeenCalledTimes(1);
    expect(addToast).toHaveBeenCalledWith('editor.versionRolledBack', 'success');
  });

  it('surfaces per-scene error codes when a rollback partially fails', async () => {
    svc.listCommits.mockResolvedValue([commit({ id: '500' })]);
    svc.rollbackCommit.mockResolvedValue(
      rollback({
        partial_failure: true,
        results: [
          { scene_id: '200', status: 'rolled_back' },
          { scene_id: '201', status: 'failed', error_code: 'conflict', error: 'version_conflict' },
        ],
      }),
    );
    render(<VersionPanel scriptId="1" onCompare={vi.fn()} onRolledBack={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId('version-item')).toBeInTheDocument());

    fireEvent.click(screen.getByText('editor.versionRollback')); // arm
    await act(async () => {
      fireEvent.click(screen.getByText('editor.versionRollbackConfirm')); // confirm
    });

    expect(await screen.findByTestId('rollback-partial')).toBeInTheDocument();
    // The one failed scene's error code renders in the inline detail list.
    expect(screen.getByText('editor.versionRollbackSceneFailed')).toBeInTheDocument();
    expect(addToast).toHaveBeenCalledWith('editor.versionRollbackPartial', 'error');
  });

  it('deletes on the second (confirming) click', async () => {
    svc.listCommits.mockResolvedValue([commit({ id: '500' })]);
    svc.deleteCommit.mockResolvedValue(undefined);
    render(<VersionPanel scriptId="1" onCompare={vi.fn()} onRolledBack={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId('version-item')).toBeInTheDocument());

    const del = screen.getByLabelText('editor.versionDelete');
    fireEvent.click(del); // arm
    expect(svc.deleteCommit).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(screen.getByText('editor.versionDeleteConfirm')); // confirm
    });
    expect(svc.deleteCommit).toHaveBeenCalledWith('500');
  });

  it('opens the diff for a commit via Compare', async () => {
    svc.listCommits.mockResolvedValue([commit({ id: '500' })]);
    const onCompare = vi.fn();
    render(<VersionPanel scriptId="1" onCompare={onCompare} onRolledBack={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId('version-item')).toBeInTheDocument());

    fireEvent.click(screen.getByText('editor.versionCompare'));
    expect(onCompare).toHaveBeenCalledWith(expect.objectContaining({ id: '500' }));
  });
});
