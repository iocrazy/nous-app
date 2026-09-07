/**
 * TDD tests for the two-phase batch import rewrite of useResourceUpload.
 *
 * Mocking strategy:
 *   - runImport  — mocked so we never hit real network; assert call args
 *   - computeFileHash — returns deterministic hash-<n> for file-<n>.txt
 *   - checkDuplicatesBatch — controlled per-test to specify dup indices
 *   - uploadResource / linkExistingResource — no-op mocks
 *
 * The hook is wrapped in UploadProvider so useUpload() resolves.
 */

import React from 'react';
import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { UploadProvider } from '../contexts/UploadContext';

// ── Module mocks (hoisted) ────────────────────────────────────────────────────

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, params?: Record<string, unknown>) =>
      params ? `${k}:${JSON.stringify(params)}` : k,
  }),
}));

// runImport mock — simulates a successful import with the given dupAction
const mockRunImport = vi.fn(async (_files: File[], _deps: unknown, opts: { onProgress: (p: { phase: string; total: number; done: number; linked: number; failed: number }) => void; dupAction: string }) => {
  opts.onProgress({ phase: 'hashing', total: 1, done: 1, linked: 0, failed: 0 });
  opts.onProgress({ phase: 'transferring', total: 1, done: 1, linked: opts.dupAction === 'skip-link' ? 1 : 0, failed: 0 });
  return {
    uploaded: opts.dupAction === 'skip-link' ? 0 : 1,
    linked: opts.dupAction === 'skip-link' ? 1 : 0,
    failed: 0,
    total: 1,
  };
});

vi.mock('../utils/importPipeline', () => ({
  runImport: (...args: Parameters<typeof mockRunImport>) => mockRunImport(...args),
}));

const mockComputeFileHash = vi.fn(async (f: File): Promise<string> => {
  const idx = parseInt(f.name.replace('file-', '').replace('.txt', ''), 10);
  return `hash-${isNaN(idx) ? f.name : idx}`;
});

vi.mock('../utils/fileHash', () => ({
  computeFileHash: (...args: Parameters<typeof mockComputeFileHash>) =>
    mockComputeFileHash(...args),
}));

const mockCheckDuplicatesBatch = vi.fn(async (
  items: { file_hash: string; file_size: number }[],
): Promise<{ file_hash: string; duplicate: boolean; existing: { id: string } | null }[]> =>
  items.map(({ file_hash }) => ({ file_hash, duplicate: false, existing: null })),
);

const mockUploadResource = vi.fn(async () => ({} as never));
const mockLinkExistingResource = vi.fn(async () => ({} as never));
const mockCheckDuplicate = vi.fn(async () => ({ duplicate: false, existing: null }));

vi.mock('../services/resourceService', () => ({
  checkDuplicatesBatch: (...args: Parameters<typeof mockCheckDuplicatesBatch>) =>
    mockCheckDuplicatesBatch(...args),
  uploadResource: (...args: Parameters<typeof mockUploadResource>) =>
    mockUploadResource(...args),
  linkExistingResource: (...args: Parameters<typeof mockLinkExistingResource>) =>
    mockLinkExistingResource(...args),
  checkDuplicate: (...args: Parameters<typeof mockCheckDuplicate>) =>
    mockCheckDuplicate(...args),
}));

// ── Helpers ───────────────────────────────────────────────────────────────────

import { useResourceUpload } from './useResourceUpload';

function makeFile(name: string, type = 'text/plain'): File {
  return new File(['content'], name, { type });
}

const addToastSpy = vi.fn();
const reloadResourcesSpy = vi.fn(async () => {});

const defaultOpts = {
  scopeId: 'scope-1',
  selectedFolderId: null as null | string,
  selectedLibraryId: null as null | string,
  reloadResources: reloadResourcesSpy,
  addToast: addToastSpy,
};

const wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <UploadProvider>{children}</UploadProvider>
);

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('useResourceUpload — two-phase batch import', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default: no dups
    mockCheckDuplicatesBatch.mockImplementation(
      async (items) =>
        items.map(({ file_hash }) => ({ file_hash, duplicate: false, existing: null })),
    );
    // Default: runImport resolves with 1 uploaded
    mockRunImport.mockImplementation(async (_files, _deps, opts) => {
      opts.onProgress({ phase: 'hashing', total: 1, done: 1, linked: 0, failed: 0 });
      opts.onProgress({ phase: 'transferring', total: 1, done: 1, linked: 0, failed: 0 });
      return { uploaded: 1, linked: 0, failed: 0, total: 1 };
    });
    reloadResourcesSpy.mockResolvedValue(undefined);
  });

  // ── Validation ────────────────────────────────────────────────────────────

  it('blocked-ext file becomes an error item and valid files still proceed', async () => {
    const { result } = renderHook(() => useResourceUpload(defaultOpts), { wrapper });

    // Provide one blocked file and one valid file
    const files = [makeFile('malware.exe'), makeFile('file-0.txt')];

    await act(async () => {
      await result.current.handleUpload(files);
    });

    // runImport is still called for the valid file
    expect(mockRunImport).toHaveBeenCalledTimes(1);
    // reloadResources called once at the end
    expect(reloadResourcesSpy).toHaveBeenCalledTimes(1);
  });

  it('when all files are invalid, runImport is never called', async () => {
    const { result } = renderHook(() => useResourceUpload(defaultOpts), { wrapper });

    await act(async () => {
      await result.current.handleUpload([makeFile('bad.exe')]);
    });

    expect(mockRunImport).not.toHaveBeenCalled();
    expect(reloadResourcesSpy).not.toHaveBeenCalled();
  });

  // ── No-dup path ───────────────────────────────────────────────────────────

  it('when no dups, batchDupDecision stays null and runImport is called directly', async () => {
    const { result } = renderHook(() => useResourceUpload(defaultOpts), { wrapper });

    // no dups by default (mockCheckDuplicatesBatch returns all non-dup)
    let uploadPromise!: Promise<void>;
    act(() => {
      uploadPromise = result.current.handleUpload([makeFile('file-0.txt')]);
    });

    await act(async () => { await uploadPromise; });

    // No modal shown — batchDupDecision never set (ends as null)
    expect(result.current.batchDupDecision).toBeNull();
    expect(mockRunImport).toHaveBeenCalledTimes(1);
    // dupAction should be 'skip-link' (default when no dups — result is same)
    expect(mockRunImport).toHaveBeenCalledWith(
      expect.any(Array),
      expect.objectContaining({ hashFile: expect.any(Function) }),
      expect.objectContaining({ dupAction: 'skip-link' }),
    );
  });

  // ── Dup upfront decision ──────────────────────────────────────────────────

  it('when dups exist, batchDupDecision is set with correct dupCount and totalCount', async () => {
    // Mark file-0.txt as a dup
    mockCheckDuplicatesBatch.mockResolvedValueOnce([
      { file_hash: 'hash-0', duplicate: true, existing: { id: 'existing-0' } },
    ]);

    const { result } = renderHook(() => useResourceUpload(defaultOpts), { wrapper });

    act(() => {
      void result.current.handleUpload([makeFile('file-0.txt')]);
    });

    // Wait for batchDupDecision to be set (after pre-pass completes)
    await waitFor(() => expect(result.current.batchDupDecision).not.toBeNull());

    expect(result.current.batchDupDecision!.dupCount).toBe(1);
    expect(result.current.batchDupDecision!.totalCount).toBe(1);
  });

  it('choosing skip-link calls runImport with dupAction=skip-link', async () => {
    mockCheckDuplicatesBatch.mockResolvedValueOnce([
      { file_hash: 'hash-0', duplicate: true, existing: { id: 'existing-0' } },
    ]);
    mockRunImport.mockImplementationOnce(async (_f, _d, opts) => {
      opts.onProgress({ phase: 'transferring', total: 1, done: 1, linked: 1, failed: 0 });
      return { uploaded: 0, linked: 1, failed: 0, total: 1 };
    });

    const { result } = renderHook(() => useResourceUpload(defaultOpts), { wrapper });

    act(() => { void result.current.handleUpload([makeFile('file-0.txt')]); });

    await waitFor(() => expect(result.current.batchDupDecision).not.toBeNull());

    // User chooses "skip-link"
    await act(async () => {
      result.current.batchDupDecision!.resolve('skip-link');
    });

    await waitFor(() => expect(mockRunImport).toHaveBeenCalledTimes(1));
    expect(mockRunImport).toHaveBeenCalledWith(
      expect.any(Array),
      expect.any(Object),
      expect.objectContaining({ dupAction: 'skip-link' }),
    );
  });

  it('choosing upload calls runImport with dupAction=upload', async () => {
    mockCheckDuplicatesBatch.mockResolvedValueOnce([
      { file_hash: 'hash-0', duplicate: true, existing: { id: 'existing-0' } },
    ]);
    mockRunImport.mockImplementationOnce(async (_f, _d, opts) => {
      opts.onProgress({ phase: 'transferring', total: 1, done: 1, linked: 0, failed: 0 });
      return { uploaded: 1, linked: 0, failed: 0, total: 1 };
    });

    const { result } = renderHook(() => useResourceUpload(defaultOpts), { wrapper });

    act(() => { void result.current.handleUpload([makeFile('file-0.txt')]); });

    await waitFor(() => expect(result.current.batchDupDecision).not.toBeNull());

    await act(async () => {
      result.current.batchDupDecision!.resolve('upload');
    });

    await waitFor(() => expect(mockRunImport).toHaveBeenCalledTimes(1));
    expect(mockRunImport).toHaveBeenCalledWith(
      expect.any(Array),
      expect.any(Object),
      expect.objectContaining({ dupAction: 'upload' }),
    );
  });

  it('choosing cancel aborts and runImport is never called', async () => {
    mockCheckDuplicatesBatch.mockResolvedValueOnce([
      { file_hash: 'hash-0', duplicate: true, existing: { id: 'existing-0' } },
    ]);

    const { result } = renderHook(() => useResourceUpload(defaultOpts), { wrapper });

    act(() => { void result.current.handleUpload([makeFile('file-0.txt')]); });

    await waitFor(() => expect(result.current.batchDupDecision).not.toBeNull());

    await act(async () => {
      result.current.batchDupDecision!.resolve('cancel');
    });

    // runImport must NOT be called
    await waitFor(() => expect(result.current.batchDupDecision).toBeNull());
    expect(mockRunImport).not.toHaveBeenCalled();
    expect(reloadResourcesSpy).not.toHaveBeenCalled();
  });

  // ── bulkSummary lifecycle ─────────────────────────────────────────────────

  it('bulkSummary is non-null while importing and cleared when done', async () => {
    const summaryStates: Array<{ phase: string } | null> = [];

    // We need to capture bulkSummary snapshots during the runImport call.
    // We do this by having runImport call onProgress and we observe the result
    // from the context. Use a deferred approach:
    let resolveImport!: () => void;
    mockRunImport.mockImplementationOnce(async (_f, _d, opts) => {
      opts.onProgress({ phase: 'transferring', total: 1, done: 0, linked: 0, failed: 0 });
      await new Promise<void>((r) => { resolveImport = r; });
      opts.onProgress({ phase: 'transferring', total: 1, done: 1, linked: 0, failed: 0 });
      return { uploaded: 1, linked: 0, failed: 0, total: 1 };
    });

    const { result } = renderHook(() => useResourceUpload(defaultOpts), { wrapper });

    let uploadDone!: Promise<void>;
    act(() => {
      uploadDone = result.current.handleUpload([makeFile('file-0.txt')]);
    });

    // Wait for runImport to be in-flight
    await waitFor(() => expect(mockRunImport).toHaveBeenCalledTimes(1));

    // bulkSummary should be set while in progress
    await waitFor(() => {
      summaryStates.push(result.current.upload.bulkSummary);
      expect(result.current.upload.bulkSummary).not.toBeNull();
    });

    // Resolve the import
    await act(async () => {
      resolveImport();
      await uploadDone;
    });

    // bulkSummary cleared after completion
    await waitFor(() => expect(result.current.upload.bulkSummary).toBeNull());
  });

  // ── reloadResources ───────────────────────────────────────────────────────

  it('reloadResources is called exactly once after a successful import', async () => {
    const { result } = renderHook(() => useResourceUpload(defaultOpts), { wrapper });

    await act(async () => {
      await result.current.handleUpload([makeFile('file-0.txt')]);
    });

    expect(reloadResourcesSpy).toHaveBeenCalledTimes(1);
  });

  it('reloadResources is called even when runImport reports some failures', async () => {
    mockRunImport.mockResolvedValueOnce({ uploaded: 0, linked: 0, failed: 1, total: 1 });

    const { result } = renderHook(() => useResourceUpload(defaultOpts), { wrapper });

    await act(async () => {
      await result.current.handleUpload([makeFile('file-0.txt')]);
    });

    expect(reloadResourcesSpy).toHaveBeenCalledTimes(1);
  });

  // ── Cached hash function ──────────────────────────────────────────────────

  it('computeFileHash is called exactly once per file (cache prevents double-hash)', async () => {
    const { result } = renderHook(() => useResourceUpload(defaultOpts), { wrapper });

    // Two files
    const files = [makeFile('file-0.txt'), makeFile('file-1.txt')];

    await act(async () => {
      await result.current.handleUpload(files);
    });

    // computeFileHash called once per file in the pre-pass.
    // runImport's cached hashFile returns from the map without calling computeFileHash again.
    expect(mockComputeFileHash).toHaveBeenCalledTimes(2);
  });

  // ── Drag-and-drop API surface ─────────────────────────────────────────────

  it('hook exposes drag-and-drop handlers and refs', () => {
    const { result } = renderHook(() => useResourceUpload(defaultOpts), { wrapper });
    expect(typeof result.current.handleDragEnter).toBe('function');
    expect(typeof result.current.handleDragOver).toBe('function');
    expect(typeof result.current.handleDragLeave).toBe('function');
    expect(typeof result.current.handleDrop).toBe('function');
    expect(result.current.fileInputRef).toBeDefined();
    expect(result.current.folderInputRef).toBeDefined();
  });

  // ── Toast summary ─────────────────────────────────────────────────────────

  /**
   * 2026-09-07: when every file fails for a reason the backend spelled out
   * (an object-store write failure answers 503 with one sentence), the
   * summary toast must repeat that sentence and be an error — "3 failed"
   * with an info badge told the user nothing about what to do.
   */
  it('toast repeats the backend\'s reason when uploads fail with one', async () => {
    const SENTENCE = 'Storage is unavailable, so nothing was saved. Try again in a moment.';
    mockUploadResource.mockImplementation(async () => {
      throw Object.assign(new Error(SENTENCE), {
        name: 'ResourceUploadError',
        reason: 'server',
        status: 503,
        detail: SENTENCE,
      });
    });
    mockRunImport.mockImplementationOnce(async (files, deps) => {
      const d = deps as { upload: (f: File) => Promise<void> };
      for (const f of files) await d.upload(f).catch(() => undefined);
      return { uploaded: 0, linked: 0, failed: files.length, total: files.length };
    });

    const { result } = renderHook(() => useResourceUpload(defaultOpts), { wrapper });

    await act(async () => {
      await result.current.handleUpload([makeFile('file-0.txt'), makeFile('file-1.txt')]);
    });

    expect(addToastSpy).toHaveBeenCalledWith(
      expect.stringContaining('importDoneWithReason'),
      'error',
    );
    expect(addToastSpy).toHaveBeenCalledWith(expect.stringContaining(SENTENCE), 'error');
  });

  it('toast is fired with importDone key after completion', async () => {
    mockRunImport.mockResolvedValueOnce({ uploaded: 2, linked: 1, failed: 0, total: 3 });

    const { result } = renderHook(() => useResourceUpload(defaultOpts), { wrapper });

    await act(async () => {
      await result.current.handleUpload([
        makeFile('file-0.txt'),
        makeFile('file-1.txt'),
        makeFile('file-2.txt'),
      ]);
    });

    expect(addToastSpy).toHaveBeenCalledWith(
      expect.stringContaining('importDone'),
      'success',
    );
  });
});
