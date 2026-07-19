import { renderHook, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// t returns the provided default so labels match the English UI text.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, def?: string) => def ?? _key }),
}));

// resourceService is imported at module load; stub its named exports so the
// hook module loads without touching the network / env.
vi.mock('../services/resourceService', () => ({
  getFolderContentCount: vi.fn(),
  getResourceFileUrl: vi.fn(() => 'blob:x'),
  copyResourceItem: vi.fn(),
  generateGenPrompt: vi.fn(),
  classifyResource: vi.fn(),
}));

const { fetchResourceTags } = vi.hoisted(() => ({ fetchResourceTags: vi.fn() }));
vi.mock('../services/unifiedTagService', () => ({ fetchResourceTags }));

const { toggleToPublish } = vi.hoisted(() => ({ toggleToPublish: vi.fn() }));
vi.mock('../services/toPublishService', async (orig) => ({
  ...(await orig<typeof import('../services/toPublishService')>()),
  toggleToPublish,
}));

import { useContextMenuItems } from './useContextMenuItems';

const addToast = vi.fn();

function buildOptions(target: any) {
  const noop = vi.fn();
  return {
    contextMenu: { x: 0, y: 0, type: 'file' as const, target },
    isPersonal: true,
    scopeId: 's1',
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
    addToast,
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

const videoRow = (tags?: any[]) => ({
  id: 'item-1',
  resource: { id: 'res-1', filename: 'clip.mp4', file_type: 'video', mime_type: 'video/mp4', tags },
});

const imageRow = () => ({
  id: 'item-2',
  resource: { id: 'res-2', filename: 'pic.png', file_type: 'image', mime_type: 'image/png', tags: [] },
});

const labels = (items: any[]) => items.map((i) => i.label);

beforeEach(() => {
  vi.clearAllMocks();
});

// The options (and its `contextMenu`) must keep a STABLE identity across
// re-renders — the mark-resolving effect keys on `contextMenu`, and a fresh
// object each render would re-fire it into a render loop. In the real app
// `contextMenu` is stable React state, so building opts once mirrors that.
describe('useContextMenuItems — Mark to publish', () => {
  it('shows "Mark to Publish" for an unmarked video (joined tags)', async () => {
    const opts = buildOptions(videoRow([]));
    const { result } = renderHook(() => useContextMenuItems(opts));
    await waitFor(() => expect(labels(result.current)).toContain('Mark to Publish'));
    expect(labels(result.current)).not.toContain('Unmark to Publish');
  });

  it('shows "Unmark to Publish" when the video already carries the tag', async () => {
    const opts = buildOptions(videoRow([{ name: 'To Publish' }]));
    const { result } = renderHook(() => useContextMenuItems(opts));
    await waitFor(() => expect(labels(result.current)).toContain('Unmark to Publish'));
  });

  it('does not show the mark item for non-video resources', async () => {
    const opts = buildOptions(imageRow());
    const { result } = renderHook(() => useContextMenuItems(opts));
    // let the effect settle
    await waitFor(() => expect(labels(result.current)).toContain('View Details'));
    expect(labels(result.current)).not.toContain('Mark to Publish');
    expect(labels(result.current)).not.toContain('Unmark to Publish');
  });

  it('resolves the mark asynchronously when the row has no joined tags', async () => {
    fetchResourceTags.mockResolvedValue([{ tag: { name: 'To Publish' } }]);
    const opts = buildOptions(videoRow(undefined));
    const { result } = renderHook(() => useContextMenuItems(opts));
    await waitFor(() => expect(fetchResourceTags).toHaveBeenCalledWith('res-1'));
    await waitFor(() => expect(labels(result.current)).toContain('Unmark to Publish'));
  });

  it('calls toggleToPublish with the current marked state on click', async () => {
    toggleToPublish.mockResolvedValue(true);
    const opts = buildOptions(videoRow([]));
    const { result } = renderHook(() => useContextMenuItems(opts));
    await waitFor(() => expect(labels(result.current)).toContain('Mark to Publish'));
    const item = result.current.find((i: any) => i.label === 'Mark to Publish');
    await item.onClick();
    expect(toggleToPublish).toHaveBeenCalledWith('res-1', false);
    expect(addToast).toHaveBeenCalledWith('Marked to publish', 'success');
  });
});
