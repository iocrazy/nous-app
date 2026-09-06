/**
 * useResourceOperations.move.test.tsx
 *
 * `moveBatch.test.ts` covers the two pure helpers. What it cannot cover is the
 * property the ticket actually promises: that the refusal happens BEFORE the
 * first write, that a locked folder is never handed to `moveFolder`, and that
 * a refusal from the server is echoed rather than swallowed.
 *
 * That ordering is the whole fix. Move the partition below the loop and every
 * helper test stays green while the original bug is back — a system folder
 * relocated, or (since mig 457) a batch that fails with the user told nothing.
 * So these cases assert on call ORDER and on what the mocks were called with,
 * not just on the final toast.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, def?: unknown) => (typeof def === 'string' ? def : key) }),
}));

const {
  moveFolder,
  moveResourceItem,
  moveResourceItems,
  renameFolder,
  copyResourceItem,
} = vi.hoisted(() => ({
  moveFolder: vi.fn(),
  moveResourceItem: vi.fn(),
  moveResourceItems: vi.fn(),
  renameFolder: vi.fn(),
  copyResourceItem: vi.fn(),
}));

vi.mock('../services/resourceService', () => ({
  moveFolder,
  moveResourceItem,
  moveResourceItems,
  renameFolder,
  copyResourceItem,
  renameResource: vi.fn(),
  trashResources: vi.fn(),
  createSmartFolder: vi.fn(),
  updateSmartFolder: vi.fn(),
  deleteSmartFolder: vi.fn(),
  fetchSmartFolders: vi.fn().mockResolvedValue([]),
}));

import { useResourceOperations } from './useResourceOperations';
import type { Folder } from '../types';

const folderRow = (over: Partial<Folder>): Folder => ({
  id: '742318905233408001',
  name: 'Folder',
  parent_id: null,
  library_id: null,
  scope_id: '742318905233407001',
  created_by: '00000000-0000-4000-8000-000000000001',
  sort_order: 0,
  is_system: false,
  system_key: null,
  icon: null,
  color: null,
  visibility: 'inherited',
  is_smart: false,
  smart_rules: null,
  is_trashed: false,
  trashed_at: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...over,
});

const PLAIN = folderRow({ id: '742318905233408004', name: 'Reference Boards' });
const CHAT_UPLOADS = folderRow({
  id: '742318905233408002',
  name: 'Chat Uploads',
  is_system: true,
  system_key: 'chat_uploads',
});

// Real PostgREST wire shape for mig 457's
// `RAISE EXCEPTION 'system_folder' USING ERRCODE = 'check_violation',
//  HINT = 'system_folder'` — what supabase-js hands to the catch.
const TRIGGER_REFUSAL = {
  code: '23514',
  message: 'system_folder',
  details:
    'folder 742318905233408002 (chat_uploads) is a system folder and cannot be renamed, moved or trashed',
  hint: 'system_folder',
};

const LOCKED_TOAST = 'resources.systemFolderLocked';

let addToast: ReturnType<typeof vi.fn>;
let loadFolders: ReturnType<typeof vi.fn>;
let loadChildFolders: ReturnType<typeof vi.fn>;
let reloadResources: ReturnType<typeof vi.fn>;

function buildOptions(childFolders: Folder[] = []) {
  const noop = vi.fn();
  return {
    isPersonal: true,
    scopeId: '742318905233407001',
    selectedFolderId: null,
    selectedLibraryId: null,
    resources: [],
    childFolders,
    sortedItems: [],
    selectedIds: new Set<string>(),
    allSelectableIds: [],
    isResourcesView: true,
    navigate: noop,
    resPath: (p: string) => p,
    setResources: noop,
    setSmartFolders: noop,
    setSelectedIds: noop,
    loadFolders,
    loadChildFolders,
    reloadResources,
    addToast,
  } as unknown as Parameters<typeof useResourceOperations>[0];
}

/** Drive a Move of `folders` through the folder picker and return the hook. */
async function runMove(folders: Folder[], childFolders: Folder[] = []) {
  const { result } = renderHook(() => useResourceOperations(buildOptions(childFolders)));
  act(() => {
    result.current.setFolderPickerMode('move');
    result.current.setOperationTargetFolders(folders);
  });
  await act(async () => {
    await result.current.handleFolderPickerConfirm(null);
  });
  return result;
}

/** Toast messages of a given type, in the order they were raised. */
const toastsOfType = (type: string) =>
  addToast.mock.calls.filter((c) => c[1] === type).map((c) => c[0]);

beforeEach(() => {
  vi.clearAllMocks();
  addToast = vi.fn();
  loadFolders = vi.fn().mockResolvedValue(undefined);
  loadChildFolders = vi.fn().mockResolvedValue(undefined);
  reloadResources = vi.fn().mockResolvedValue(undefined);
  moveFolder.mockResolvedValue(undefined);
  renameFolder.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('handleFolderPickerConfirm — move', () => {
  it('refuses the locked folder BEFORE moving anything, and never moves it', async () => {
    await runMove([CHAT_UPLOADS, PLAIN]);

    // Ordering is the property under test: invocationCallOrder lets us say
    // "the toast came first" rather than merely "both happened".
    const refusal = addToast.mock.calls.findIndex((c) => c[0] === LOCKED_TOAST);
    expect(refusal).toBeGreaterThanOrEqual(0);
    expect(addToast.mock.invocationCallOrder[refusal]).toBeLessThan(
      moveFolder.mock.invocationCallOrder[0],
    );

    // Only the movable one was written, and the locked id never appears.
    expect(moveFolder).toHaveBeenCalledTimes(1);
    expect(moveFolder).toHaveBeenCalledWith(PLAIN.id, null, undefined);
    const movedIds = moveFolder.mock.calls.map((c) => c[0]);
    expect(movedIds).not.toContain(CHAT_UPLOADS.id);

    // The one that did move is still reported as moved.
    expect(toastsOfType('success')).toEqual(['resources.moveSuccess']);
  });

  it('echoes the DB trigger refusal as the typed toast, and still reloads', async () => {
    moveFolder.mockRejectedValueOnce(TRIGGER_REFUSAL);

    await runMove([PLAIN]);

    expect(toastsOfType('error')).toEqual([LOCKED_TOAST]);
    expect(toastsOfType('success')).toEqual([]);
    // In `finally`: a batch that failed halfway must still show what moved.
    expect(loadFolders).toHaveBeenCalled();
    expect(loadChildFolders).toHaveBeenCalled();
    expect(reloadResources).toHaveBeenCalled();
  });

  it('reports an unrecognised failure generically and logs it', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const boom = new Error('boom');
    moveFolder.mockRejectedValueOnce(boom);

    await runMove([PLAIN]);

    expect(toastsOfType('error')).toEqual([
      'Move failed — nothing was changed for the remaining items',
    ]);
    expect(toastsOfType('success')).toEqual([]);
    expect(consoleError).toHaveBeenCalledWith(
      '[useResourceOperations] move failed:',
      boom,
    );
  });

  it('shows no success toast when the whole selection was locked', async () => {
    await runMove([CHAT_UPLOADS]);

    expect(moveFolder).not.toHaveBeenCalled();
    expect(toastsOfType('error')).toEqual([LOCKED_TOAST]);
    // "Moved 0 files" would contradict the refusal the user just read.
    expect(toastsOfType('success')).toEqual([]);
  });
});

describe('handleRenameFolderConfirm', () => {
  it('refuses a system folder without calling renameFolder', async () => {
    const { result } = renderHook(() =>
      useResourceOperations(buildOptions([CHAT_UPLOADS])),
    );
    act(() => {
      result.current.setRenamingFolderId(CHAT_UPLOADS.id);
      result.current.setRenameFolderValue('Renamed');
    });
    await act(async () => {
      await result.current.handleRenameFolderConfirm();
    });

    expect(renameFolder).not.toHaveBeenCalled();
    expect(toastsOfType('error')).toEqual([LOCKED_TOAST]);
    expect(toastsOfType('success')).toEqual([]);
  });

  it('echoes a server refusal for a folder it could not pre-check', async () => {
    // Not in `childFolders`, so the pre-refuse cannot see it — this is the
    // arm that keeps mig 457's throw from landing in a silent catch.
    renameFolder.mockRejectedValueOnce(TRIGGER_REFUSAL);
    const { result } = renderHook(() => useResourceOperations(buildOptions([])));
    act(() => {
      result.current.setRenamingFolderId(CHAT_UPLOADS.id);
      result.current.setRenameFolderValue('Renamed');
    });
    await act(async () => {
      await result.current.handleRenameFolderConfirm();
    });

    expect(renameFolder).toHaveBeenCalledTimes(1);
    expect(toastsOfType('error')).toEqual([LOCKED_TOAST]);
    expect(toastsOfType('success')).toEqual([]);
  });

  it('reports an unrecognised rename failure generically and logs it', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const boom = new Error('boom');
    renameFolder.mockRejectedValueOnce(boom);
    const { result } = renderHook(() => useResourceOperations(buildOptions([PLAIN])));
    act(() => {
      result.current.setRenamingFolderId(PLAIN.id);
      result.current.setRenameFolderValue('Renamed');
    });
    await act(async () => {
      await result.current.handleRenameFolderConfirm();
    });

    expect(toastsOfType('error')).toEqual(['Rename Failed']);
    expect(consoleError).toHaveBeenCalledWith(
      '[useResourceOperations] rename failed:',
      boom,
    );
  });

  it('still renames a plain folder and says so', async () => {
    const { result } = renderHook(() => useResourceOperations(buildOptions([PLAIN])));
    act(() => {
      result.current.setRenamingFolderId(PLAIN.id);
      result.current.setRenameFolderValue('Renamed');
    });
    await act(async () => {
      await result.current.handleRenameFolderConfirm();
    });

    expect(renameFolder).toHaveBeenCalledWith(PLAIN.id, 'Renamed');
    expect(toastsOfType('success')).toEqual(['resources.renamedNotification']);
    expect(toastsOfType('error')).toEqual([]);
  });
});
