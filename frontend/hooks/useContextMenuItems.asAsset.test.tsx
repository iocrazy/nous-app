/**
 * useContextMenuItems.asAsset.test.tsx
 *
 * "As Asset" on a My Uploads file (P6 ruling E).
 *
 * The entry mirrors the backend's accepted set — image and audio, the only two
 * file shapes an asset slot takes — so the menu never offers an action whose
 * only possible answer is a typed 422. Both halves are pinned: a test that only
 * checked the absence would pass on a menu that had lost the entry entirely.
 *
 * The kind comes from `resourceKind`, the repo's ONE mime→kind ladder, so it is
 * deliberately NOT mocked here: a stub would let this suite stay green while
 * the real ladder classified an image as a doc.
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
import type { Resource, ResourceItem } from '../types';

const AS_ASSET = 'As Asset';
const SEND_TO_AGENT = 'Send to Agent';

const resource = (over: Partial<Resource>): Resource =>
  ({
    id: '742318905233409001',
    creator_id: '00000000-0000-4000-8000-000000000001',
    source_type: 'upload',
    media_id: null,
    filename: 'file.bin',
    file_type: null,
    mime_type: null,
    file_path: '/data/file.bin',
    file_size_bytes: 10,
    duration_seconds: null,
    resolution: null,
    thumbnail_path: null,
    cover_image_path: null,
    current_version: 1,
    notes: null,
    gen_prompt: null,
    url: null,
    rating: 0,
    is_trashed: false,
    trashed_at: null,
    created_at: '2026-09-01T00:00:00Z',
    ...over,
  }) as Resource;

const itemFor = (over: Partial<Resource>): ResourceItem =>
  ({
    id: '742318905233500001',
    resource_id: '742318905233409001',
    scope_id: '742318905233407001',
    folder_id: null,
    library_id: null,
    added_by: null,
    created_at: '2026-09-01T00:00:00Z',
    resource: resource(over),
  }) as ResourceItem;

function buildOptions(target: ResourceItem, onSaveAsAsset: () => void) {
  const noop = vi.fn();
  return {
    contextMenu: { x: 0, y: 0, type: 'file' as const, target },
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
    onSaveAsAsset,
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

const labels = (items: any[]) => items.map((i) => i.label);

beforeEach(() => {
  vi.clearAllMocks();
  fetchResourceTags.mockResolvedValue([]);
});

describe('useContextMenuItems — As Asset', () => {
  it.each([
    ['an image', { mime_type: 'image/png', file_type: 'image', filename: 'a.png' }],
    ['audio', { mime_type: 'audio/mpeg', file_type: 'audio', filename: 'a.mp3' }],
    // Mime absent, `file_type` carries the kind: the ladder's fallback arm.
    ['an image known only by file_type', { mime_type: null, file_type: 'image' }],
  ])('offers it on %s', async (_name, over) => {
    const onSaveAsAsset = vi.fn();
    const opts = buildOptions(itemFor(over as Partial<Resource>), onSaveAsAsset);
    const { result } = renderHook(() => useContextMenuItems(opts));

    await waitFor(() => expect(labels(result.current)).toContain(AS_ASSET));
    // Placement is part of the contract: straight after Send to Agent.
    const order = labels(result.current);
    expect(order.indexOf(AS_ASSET)).toBe(order.indexOf(SEND_TO_AGENT) + 1);
  });

  it.each([
    ['video', { mime_type: 'video/mp4', file_type: 'video', filename: 'a.mp4' }],
    ['a PDF', { mime_type: 'application/pdf', file_type: 'pdf', filename: 'a.pdf' }],
    ['a document', { mime_type: 'text/plain', file_type: 'doc', filename: 'a.txt' }],
    // A download row: `file_type` holds an aweme numeral, not a kind.
    ['an unclassifiable download', { mime_type: null, file_type: '68' }],
  ])('omits it on %s', async (_name, over) => {
    const onSaveAsAsset = vi.fn();
    const opts = buildOptions(itemFor(over as Partial<Resource>), onSaveAsAsset);
    const { result } = renderHook(() => useContextMenuItems(opts));

    // Wait for a stable menu before asserting an absence, so this cannot pass
    // merely by looking too early.
    await waitFor(() => expect(labels(result.current)).toContain(SEND_TO_AGENT));
    expect(labels(result.current)).not.toContain(AS_ASSET);
    expect(onSaveAsAsset).not.toHaveBeenCalled();
  });

  it('hands the clicked item to the host, which owns the dialog', async () => {
    const onSaveAsAsset = vi.fn();
    const item = itemFor({ mime_type: 'image/png', file_type: 'image', filename: 'a.png' });
    const opts = buildOptions(item, onSaveAsAsset);
    const { result } = renderHook(() => useContextMenuItems(opts));

    await waitFor(() => expect(labels(result.current)).toContain(AS_ASSET));
    result.current.find((i) => i.label === AS_ASSET)!.onClick();

    expect(onSaveAsAsset).toHaveBeenCalledTimes(1);
    expect(onSaveAsAsset).toHaveBeenCalledWith(item);
  });

  it('is disabled, and calls nothing, for a row with no resource id', async () => {
    const onSaveAsAsset = vi.fn();
    const item = itemFor({ mime_type: 'image/png', file_type: 'image' });
    // The joined resource is there (so the kind still resolves) but its id is
    // not — the shape a half-loaded row really takes.
    (item.resource as any).id = undefined;
    const opts = buildOptions(item, onSaveAsAsset);
    const { result } = renderHook(() => useContextMenuItems(opts));

    await waitFor(() => expect(labels(result.current)).toContain(AS_ASSET));
    const entry = result.current.find((i) => i.label === AS_ASSET)!;
    expect(entry.disabled).toBe(true);
    entry.onClick();
    expect(onSaveAsAsset).not.toHaveBeenCalled();
  });
});
