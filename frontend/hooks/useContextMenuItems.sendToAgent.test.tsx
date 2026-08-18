/**
 * "Send to Agent" in the resource context menu (spec F3).
 *
 * The expensive property under test is NOT that the menu item exists: it is
 * that the click hands the resource's *processing status* to the helper.
 * The transcribe endpoint dedups in-flight work only — it does not dedup
 * finished work — and then charges points. So a Send to Agent that forgets
 * the status columns re-transcribes, and re-bills, every already-processed
 * video the user right-clicks.
 *
 * `ensureResourceProcessed` is therefore NOT mocked here; the two trigger
 * services underneath it are, so the assertions are about which HTTP calls
 * the click really makes.
 */
import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, def?: string, opts?: Record<string, unknown>) =>
      (def ?? _key).replace(/\{\{(\w+)\}\}/g, (_m, n) => String(opts?.[n] ?? '')),
  }),
}));

vi.mock('../services/resourceService', () => ({
  getFolderContentCount: vi.fn(),
  getResourceFileUrl: vi.fn(() => 'blob:x'),
  copyResourceItem: vi.fn(),
  generateGenPrompt: vi.fn(),
  classifyResource: vi.fn(),
}));
vi.mock('../services/unifiedTagService', () => ({ fetchResourceTags: vi.fn() }));
vi.mock('../services/toPublishService', () => ({
  hasToPublishTag: () => false,
  toggleToPublish: vi.fn(),
}));

const { transcribeMock, summaryMock } = vi.hoisted(() => ({
  transcribeMock: vi.fn(),
  summaryMock: vi.fn(),
}));
vi.mock('../services/aiService', () => ({
  triggerTranscriptionByResource: (id: string) => transcribeMock(id),
  triggerSummaryByResource: (id: string) => summaryMock(id),
}));

import { useContextMenuItems } from './useContextMenuItems';
import { useGlobalChatStore } from '../stores/globalChatStore';
import { resetTranscriptionFollowUps } from '../utils/transcriptionFollowUp';

const addToast = vi.fn();

function buildOptions(target: any, over: Record<string, unknown> = {}) {
  const noop = vi.fn();
  return {
    contextMenu: { x: 0, y: 0, type: 'file' as const, target },
    isPersonal: true,
    scopeId: 'scope-1',
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
    ...over,
  } as any;
}

/** A row exactly as the resource list holds it (RECON#15: the status
 *  columns are already on `item.resource`). */
const videoRow = (over: Record<string, unknown> = {}) => ({
  id: 'item-1',
  resource: {
    id: '339710259795355',
    filename: 'clip.mp4',
    file_type: 'video',
    mime_type: 'video/mp4',
    media_id: '900001',
    thumbnail_path: null,
    cover_image_path: null,
    transcript_status: 'none',
    summary_status: 'none',
    tags: [],
    ...over,
  },
});

/** The options object (and its `contextMenu`) must keep a stable identity
 *  across re-renders — the mark-resolving effect keys on it, and a fresh
 *  object per render would loop. In the app `contextMenu` is React state. */
function renderMenu(opts: any) {
  return renderHook(() => useContextMenuItems(opts));
}

function sendToAgent(items: any[]) {
  return items.find((i) => i.label === 'Send to Agent');
}

beforeEach(() => {
  addToast.mockClear();
  transcribeMock.mockReset().mockResolvedValue({
    message: 'Transcription queued', resource_id: '339710259795355', points_charged: 5,
  });
  summaryMock.mockReset().mockResolvedValue({ message: 'Summary generation queued' });
  resetTranscriptionFollowUps();
  useGlobalChatStore.setState({ pendingResource: null, open: false });
});

describe('Send to Agent menu item', () => {
  it('is offered for a file', () => {
    const { result } = renderMenu(buildOptions(videoRow()));
    expect(sendToAgent(result.current)).toBeTruthy();
  });

  it('stages the resource on the chat channel and opens the panel', async () => {
    const { result } = renderMenu(buildOptions(videoRow()));

    await act(async () => { await sendToAgent(result.current).onClick(); });

    const staged = useGlobalChatStore.getState().pendingResource;
    expect(staged).toMatchObject({
      resourceId: '339710259795355',
      name: 'clip.mp4',
      kind: 'video',
      mime: 'video/mp4',
      scope: { type: 'personal', id: 'scope-1' },
      transcriptStatus: 'none',
      summaryStatus: 'none',
      thumbnailUrl: '/api/v1/resources/339710259795355/cover',
    });
    expect(useGlobalChatStore.getState().open).toBe(true);
  });

  it('does NOT re-transcribe a video that is already processed', async () => {
    const { result } = renderMenu(buildOptions(
      videoRow({ transcript_status: 'completed', summary_status: 'completed' }),
    ));

    await act(async () => { await sendToAgent(result.current).onClick(); });

    // Both trigger endpoints charge; the transcribe one only dedups work
    // that is in flight, so a needless call here is a needless bill.
    expect(transcribeMock).not.toHaveBeenCalled();
    expect(summaryMock).not.toHaveBeenCalled();
    // Still staged — processing was simply not needed.
    expect(useGlobalChatStore.getState().pendingResource).toBeTruthy();
  });

  it('tops up an unprocessed video before it reaches the agent', async () => {
    const { result } = renderMenu(buildOptions(videoRow()));

    await act(async () => { await sendToAgent(result.current).onClick(); });

    expect(transcribeMock).toHaveBeenCalledWith('339710259795355');
    await waitFor(() => expect(addToast).toHaveBeenCalledWith(
      expect.stringContaining('5'), 'info',
    ));
  });

  it('asks only for the summary when the transcript is already there', async () => {
    const { result } = renderMenu(buildOptions(
      videoRow({ transcript_status: 'completed', summary_status: 'none' }),
    ));

    await act(async () => { await sendToAgent(result.current).onClick(); });

    expect(transcribeMock).not.toHaveBeenCalled();
    expect(summaryMock).toHaveBeenCalledWith('339710259795355');
  });

  it('does not charge for an in-flight task and says so', async () => {
    transcribeMock.mockResolvedValue({
      message: 'Transcription already in progress',
      resource_id: '339710259795355',
      points_charged: 0,
    });
    const { result } = renderMenu(buildOptions(videoRow()));

    await act(async () => { await sendToAgent(result.current).onClick(); });

    await waitFor(() => expect(addToast).toHaveBeenCalledWith(
      expect.stringContaining('already'), 'info',
    ));
  });

  it('surfaces a failed trigger to the user instead of swallowing it', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    transcribeMock.mockRejectedValue(new Error('HTTP 402 Insufficient points'));
    const { result } = renderMenu(buildOptions(videoRow()));

    await act(async () => { await sendToAgent(result.current).onClick(); });

    await waitFor(() => expect(addToast).toHaveBeenCalledWith(
      expect.stringContaining('Insufficient points'), 'error',
    ));
    // A failed top-up must not swallow the send: the user still gets the
    // asset in the composer, just without fresh processing.
    expect(useGlobalChatStore.getState().pendingResource).toBeTruthy();
  });

  it('sends a non-audiovisual asset without triggering anything', async () => {
    const { result } = renderMenu(buildOptions({
      id: 'item-2',
      resource: {
        id: '42', filename: 'shot.png', file_type: 'image', mime_type: 'image/png',
        transcript_status: 'none', summary_status: 'none', tags: [],
      },
    }));

    await act(async () => { await sendToAgent(result.current).onClick(); });

    expect(transcribeMock).not.toHaveBeenCalled();
    expect(addToast).not.toHaveBeenCalled();
    expect(useGlobalChatStore.getState().pendingResource).toMatchObject({
      kind: 'image',
      // image/* resources can render their own file as the cover
      thumbnailUrl: '/api/v1/resources/42/cover',
    });
  });

  it('stages a team resource under the team scope', async () => {
    const { result } = renderMenu(
      buildOptions(videoRow(), { isPersonal: false, scopeId: 'team-7' }),
    );

    await act(async () => { await sendToAgent(result.current).onClick(); });

    expect(useGlobalChatStore.getState().pendingResource?.scope)
      .toEqual({ type: 'team', id: 'team-7' });
  });

  it('offers no cover URL when the resource has no cover signal at all', async () => {
    const { result } = renderMenu(buildOptions({
      id: 'item-3',
      resource: {
        id: '77', filename: 'notes.txt', file_type: 'doc', mime_type: 'text/plain',
        media_id: null, thumbnail_path: null, cover_image_path: null,
        transcript_status: 'none', summary_status: 'none', tags: [],
      },
    }));

    await act(async () => { await sendToAgent(result.current).onClick(); });

    expect(useGlobalChatStore.getState().pendingResource).toMatchObject({
      kind: 'doc', thumbnailUrl: null,
    });
  });
});
