// frontend/hooks/useResourceUpload.ts

/**
 * Upload hook for ResourcesView.
 *
 * Two-phase batch import pipeline:
 *   Phase 1 — hash all files (bounded concurrency) + batch-dedup → ONE
 *             upfront duplicate decision modal (no per-file prompts).
 *   Phase 2 — call runImport() with cached hashes + resolved dupAction;
 *             progress driven via bulkSummary aggregate (no 100 k DOM rows).
 *
 * Preserved: file validation, drag/drop handlers, folder/library scoping.
 */

import { useState, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useUpload, type UploadFileProgress } from '../contexts/UploadContext';
import { computeFileHash } from '../utils/fileHash';
import { runWithConcurrency } from '../utils/concurrency';
import { runImport } from '../utils/importPipeline';
import {
  uploadResource,
  checkDuplicatesBatch,
  linkExistingResource,
} from '../services/resourceService';

// ─── Constants ────────────────────────────────────────

const BLOCKED_EXTENSIONS = new Set([
  '.exe', '.bat', '.cmd', '.msi', '.scr', '.pif', '.com',
  '.sh', '.bash', '.ps1', '.vbs', '.wsf', '.jar',
]);

const MAX_FILE_SIZE = 500 * 1024 * 1024; // 500 MB
const HASH_CONCURRENCY = 8;
const CHECK_CHUNK = 100; // must match backend cap

export function validateFile(file: File): string | null {
  const ext = '.' + file.name.split('.').pop()?.toLowerCase();
  if (BLOCKED_EXTENSIONS.has(ext)) return 'invalidFileType';
  if (file.size > MAX_FILE_SIZE) return 'fileTooLarge';
  return null;
}

// ─── Types ────────────────────────────────────────────

/**
 * Legacy per-file duplicate alert (kept for backward compat with
 * ResourcesModals / ResourcesViewInner — never triggered in the new
 * batch-import path, but the type & state remain in the return shape).
 */
export interface DuplicateAlertState {
  file: File;
  existing: import('../types').Resource;
  remainingDuplicates: number;
  resolve: (decision: { action: 'use-existing' | 'keep-both' | 'cancel'; applyToAll: boolean }) => void;
}

/**
 * Upfront batch duplicate decision: shown ONCE before the transfer starts
 * when the pre-pass detects ≥1 duplicate.
 */
export interface BatchDupDecisionState {
  dupCount: number;
  totalCount: number;
  resolve: (action: 'skip-link' | 'upload' | 'cancel') => void;
}

interface UseResourceUploadOptions {
  scopeId: string;
  selectedFolderId: string | null | undefined;
  selectedLibraryId: string | null | undefined;
  /** Re-fetch the current resource list honouring active filter params. */
  reloadResources: () => Promise<void>;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

// ─── Hook ─────────────────────────────────────────────

export function useResourceUpload({
  scopeId,
  selectedFolderId,
  selectedLibraryId,
  reloadResources,
  addToast,
}: UseResourceUploadOptions) {
  const { t } = useTranslation();
  const upload = useUpload();
  const uploading = upload.isUploading;

  const [dragOver, setDragOver] = useState(false);
  // Legacy per-file modal — kept so existing callers (ResourcesModals) compile
  const [duplicateAlert, setDuplicateAlert] = useState<DuplicateAlertState | null>(null);
  // NEW: one upfront batch-level duplicate decision
  const [batchDupDecision, setBatchDupDecision] = useState<BatchDupDecisionState | null>(null);

  const dragCounterRef = useRef(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const handleUpload = useCallback(async (files: FileList | File[]) => {
    if (!files.length || uploading) return;

    // ── Validation pass ──────────────────────────────────────────────────────
    const validFiles: File[] = [];
    const errorItems: UploadFileProgress[] = [];

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const validationError = validateFile(file);
      const id = `${Date.now()}-${i}`;

      if (validationError) {
        errorItems.push({
          id,
          filename: file.name,
          percent: 0,
          status: 'error',
          error: t(`resources.${validationError}`),
          fileSize: file.size,
          bytesUploaded: 0,
          speed: 0,
        });
      } else {
        validFiles.push(file);
      }
    }

    // Surface validation errors immediately
    if (errorItems.length > 0) {
      upload.setItems((prev) => [...prev, ...errorItems]);
    }

    // Nothing valid to import
    if (validFiles.length === 0) return;

    // ── Setup ────────────────────────────────────────────────────────────────
    upload.setIsUploading(true);
    upload.setOverallProgress(0);
    upload.setUploadStartTime(Date.now());
    upload.setBulkSummary({
      total: validFiles.length,
      done: 0,
      linked: 0,
      failed: 0,
      phase: 'hashing',
    });

    // ── Phase 1a: Hash all files (bounded concurrency) ───────────────────────
    const hashCache = new Map<File, string>();
    let hashDone = 0;

    await runWithConcurrency(
      validFiles,
      HASH_CONCURRENCY,
      async (file) => {
        const hash = await computeFileHash(file);
        hashCache.set(file, hash);
        hashDone++;
        upload.setBulkSummary({
          total: validFiles.length,
          done: hashDone,
          linked: 0,
          failed: 0,
          phase: 'hashing',
        });
        return hash;
      },
    );

    // ── Phase 1b: Batch dedup check (chunks of ≤100) ─────────────────────────
    const toCheck = validFiles
      .filter((f) => hashCache.has(f))
      .map((f) => ({ file: f, hash: hashCache.get(f) as string }));

    // Per-hash dedup results — handed to runImport so Phase 2 skips a second
    // round of checkBatch HTTP calls (the batch dedup runs exactly once).
    const dupByHash = new Map<string, { duplicate: boolean; existing: { id: string } | null }>();
    let dupCount = 0;
    let checkDone = 0;

    for (let ci = 0; ci < toCheck.length; ci += CHECK_CHUNK) {
      const chunk = toCheck.slice(ci, ci + CHECK_CHUNK);
      const batchItems = chunk.map(({ file, hash }) => ({
        file_hash: hash,
        file_size: file.size,
      }));

      let batchResult: { file_hash: string; duplicate: boolean; existing: unknown }[];
      try {
        batchResult = await checkDuplicatesBatch(batchItems);
      } catch {
        // Fail-open: treat every item as non-duplicate on service error
        batchResult = batchItems.map(({ file_hash }) => ({
          file_hash,
          duplicate: false,
          existing: null,
        }));
      }

      for (const r of batchResult) {
        dupByHash.set(r.file_hash, {
          duplicate: r.duplicate,
          existing: r.existing as { id: string } | null,
        });
      }
      dupCount += batchResult.filter((r) => r.duplicate).length;
      checkDone += chunk.length;
      upload.setBulkSummary({
        total: validFiles.length,
        done: checkDone,
        linked: 0,
        failed: 0,
        phase: 'checking',
      });
    }

    // ── Upfront duplicate decision (ONE modal for the whole batch) ───────────
    let dupAction: 'skip-link' | 'upload' = 'skip-link';

    if (dupCount > 0) {
      const decision = await new Promise<'skip-link' | 'upload' | 'cancel'>((resolve) => {
        setBatchDupDecision({ dupCount, totalCount: validFiles.length, resolve });
      });
      setBatchDupDecision(null);

      if (decision === 'cancel') {
        upload.setIsUploading(false);
        upload.setOverallProgress(0);
        upload.setBulkSummary(null);
        return;
      }
      dupAction = decision;
    }

    // ── Phase 2: Transfer via runImport (cached hashes, bounded concurrency) ──
    const controller = new AbortController();

    // Reset progress counters for the transfer phase
    upload.setBulkSummary({
      total: validFiles.length,
      done: 0,
      linked: 0,
      failed: 0,
      phase: 'hashing',
    });
    upload.setOverallProgress(0);

    // Cached hash function — never re-hashes what Phase 1 already computed
    const cachedHash = (f: File): Promise<string> =>
      hashCache.has(f)
        ? Promise.resolve(hashCache.get(f) as string)
        : computeFileHash(f);

    let result: { uploaded: number; linked: number; failed: number; total: number };
    // The first reason the backend spelled out for a failed upload
    // (`ResourceUploadError.detail`, e.g. the object-store 503 sentence).
    // Recognised by `name`, not `instanceof` — see PublishPage's
    // describeUploadFailure for why crossing the module boundary that way
    // is the check that quietly stops holding.
    let firstReason: string | null = null;
    const rememberReason = (err: unknown): never => {
      if (
        firstReason === null &&
        typeof err === 'object' &&
        err !== null &&
        (err as { name?: unknown }).name === 'ResourceUploadError' &&
        typeof (err as { detail?: unknown }).detail === 'string'
      ) {
        firstReason = (err as { detail: string }).detail;
      }
      throw err;
    };
    try {
      try {
        result = await runImport(
          validFiles,
          {
            hashFile: cachedHash,
            checkBatch: checkDuplicatesBatch,
            upload: (f) =>
              uploadResource(f, scopeId, selectedFolderId, undefined, selectedLibraryId).then(
                () => undefined,
                rememberReason,
              ),
            link: (existingId) =>
              linkExistingResource(existingId, scopeId, selectedFolderId, selectedLibraryId).then(
                () => undefined,
              ),
          },
          {
            dupAction,
            signal: controller.signal,
            // Pass the Phase 1 results so runImport skips its own dedup
            // check entirely — batch dedup HTTP calls fire exactly once.
            precomputed: {
              hashByFile: hashCache,
              dupByHash,
            },
            onProgress: (p) => {
              upload.setBulkSummary({ ...p, phase: p.phase });
              upload.setOverallProgress(
                p.total ? Math.round((p.done / p.total) * 100) : 0,
              );
            },
          },
        );
      } catch {
        result = { uploaded: 0, linked: 0, failed: validFiles.length, total: validFiles.length };
      }

      // ── Finish ───────────────────────────────────────────────────────────────
      // Partial failure is informational; nothing-got-through is an error;
      // and when the backend said WHY, the toast repeats it (a count alone
      // told the user nothing about what to do).
      const counts = {
        uploaded: result.uploaded,
        linked: result.linked,
        failed: result.failed,
      };
      const severity =
        result.failed === 0 ? 'success' : result.failed === result.total ? 'error' : 'info';
      addToast(
        firstReason !== null
          ? t('resources.importDoneWithReason', { ...counts, reason: firstReason })
          : t('resources.importDone', counts),
        severity,
      );

      try {
        await reloadResources();
      } catch { /* ignore */ }
    } finally {
      upload.setIsUploading(false);
      upload.setOverallProgress(0);
      upload.setBulkSummary(null);
    }
  }, [
    scopeId,
    selectedFolderId,
    selectedLibraryId,
    uploading,
    t,
    upload,
    addToast,
    reloadResources,
  ]);

  // ─── Drag & drop handlers ──────────────────────────

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current++;
    if (e.dataTransfer.types.includes('Files')) setDragOver(true);
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current--;
    if (dragCounterRef.current === 0) setDragOver(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setDragOver(false);
    if (e.dataTransfer.files.length) handleUpload(e.dataTransfer.files);
  }, [handleUpload]);

  return {
    upload,
    uploading,
    dragOver,
    /** Legacy per-file dup alert — kept for backward compat; never triggered in new path. */
    duplicateAlert,
    setDuplicateAlert,
    /** Batch-level upfront duplicate decision (non-null only while awaiting user input). */
    batchDupDecision,
    setBatchDupDecision,
    fileInputRef,
    folderInputRef,
    handleUpload,
    handleDragEnter,
    handleDragOver,
    handleDragLeave,
    handleDrop,
  };
}
