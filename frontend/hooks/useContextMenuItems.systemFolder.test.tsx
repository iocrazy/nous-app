/**
 * useContextMenuItems.systemFolder.test.tsx
 *
 * P6 makes the chat-attachment folder visible in the My Uploads root grid
 * (`ResourceGrid.systemFolderVisibility.test.tsx`), which means its context
 * menu is now reachable for the first time. What keeps it safe is `is_system`,
 * not the removed name filter: the folders router answers rename / move /
 * trash / delete on a system folder with a typed 409 (`code=system_folder`,
 * `resources_folders_router._refuse_if_system`), so offering those entries
 * here could only ever produce a failure toast.
 *
 * These tests pin both directions. The negative half alone would pass on a
 * menu that had lost the entries for every folder, so the plain-folder case
 * is what makes the system-folder case mean anything.
 */

import { renderHook, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// t returns the provided default so labels match the English UI text.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, def?: string) => def ?? _key }),
}));

vi.mock('../services/resourceService', () => ({
  getFolderContentCount: vi.fn(),
  getResourceFileUrl: vi.fn(() => 'blob:x'),
  copyResourceItem: vi.fn(),
  generateGenPrompt: vi.fn(),
  classifyResource: vi.fn(),
}));

const { fetchResourceTags } = vi.hoisted(() => ({ fetchResourceTags: vi.fn() }));
vi.mock('../services/unifiedTagService', () => ({ fetchResourceTags }));

import { useContextMenuItems } from './useContextMenuItems';
import type { Folder } from '../types';

function buildOptions(target: Folder) {
  const noop = vi.fn();
  return {
    contextMenu: { x: 0, y: 0, type: 'folder' as const, target },
    isPersonal: true,
    scopeId: '742318905233407001',
    selectedFolderId: null,
    selectedLibraryId: null,
    navigate: noop,
    resPath: (p: string) => p,
    canDo: () => true,
    fileInputRef: { current: null },
    setCreatingFolder: noop,
    onUploadGallery: noop,
    setLoading: noop,
    setSelectedResource: noop,
    setSelectedFolder: noop,
    setShowInfoPanel: noop,
    setResources: noop,
    addToast: vi.fn(),
    loadFolders: vi.fn().mockResolvedValue(undefined),
    loadChildFolders: vi.fn().mockResolvedValue(undefined),
    reloadResources: vi.fn().mockResolvedValue(undefined),
    handleTrash: noop,
    ops: {
      setRenamingResourceId: noop, setRenameValue: noop, setVersionTargetId: noop,
      setOperationTargetItems: noop, setOperationTargetFolders: noop,
      setFolderPickerMode: noop, setShareTarget: noop, setRenamingFolderId: noop,
      setRenameFolderValue: noop, setEditingSmartFolder: noop, handleDeleteSmartFolder: noop,
      operationTargetItems: [], folderPickerMode: null,
    },
    versionInputRef: { current: null },
  } as any;
}

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

const CHAT_UPLOADS = folderRow({
  id: '742318905233408002',
  name: 'Chat Uploads',
  is_system: true,
  system_key: 'chat_uploads',
});

const PLAIN = folderRow({ id: '742318905233408004', name: 'Reference Boards' });

const LOCK_LABEL = 'System folder — cannot be renamed, moved or trashed';
const MUTATING = ['resources.rename', 'resources.moveTo', 'resources.moveToTrash'];

const labels = (items: any[]) => items.map((i) => i.label);

beforeEach(() => {
  vi.clearAllMocks();
});

describe('useContextMenuItems — Chat Uploads system folder', () => {
  it('offers no rename / move / trash and says why', async () => {
    // Build the options ONCE: the hook's mark-resolving effect keys on
    // `contextMenu`, and a fresh object per render would re-fire it into a
    // render loop. In the real app `contextMenu` is stable React state.
    const opts = buildOptions(CHAT_UPLOADS);
    const { result } = renderHook(() => useContextMenuItems(opts));

    await waitFor(() => expect(labels(result.current)).toContain('Get Info'));
    for (const label of MUTATING) {
      expect(labels(result.current)).not.toContain(label);
    }
    expect(labels(result.current)).toContain(LOCK_LABEL);
    // Reading the folder is never blocked — only the four mutations are.
    expect(labels(result.current)).toContain('resources.copyTo');
  });

  it('still offers all three on a plain folder, and no lock hint', async () => {
    const opts = buildOptions(PLAIN);
    const { result } = renderHook(() => useContextMenuItems(opts));

    await waitFor(() => expect(labels(result.current)).toContain('Get Info'));
    for (const label of MUTATING) {
      expect(labels(result.current)).toContain(label);
    }
    expect(labels(result.current)).not.toContain(LOCK_LABEL);
  });
});
