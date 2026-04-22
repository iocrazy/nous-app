// frontend/hooks/useResourceUpload.ts

/**
 * Upload hook for ResourcesView.
 * Handles file validation, duplicate detection, chunked upload, drag-and-drop.
 */

import { useState, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useUpload, type UploadFileProgress } from '../contexts/UploadContext';
import { computeFileHash } from '../utils/fileHash';
import {
  uploadResource,
  checkDuplicate,
  linkExistingResource,
} from '../services/resourceService';
import type { Resource, ResourceItem } from '../types';

// ─── Constants ────────────────────────────────────────

const BLOCKED_EXTENSIONS = new Set([
  '.exe', '.bat', '.cmd', '.msi', '.scr', '.pif', '.com',
  '.sh', '.bash', '.ps1', '.vbs', '.wsf', '.jar',
]);

const MAX_FILE_SIZE = 500 * 1024 * 1024; // 500 MB

export function validateFile(file: File): string | null {
  const ext = '.' + file.name.split('.').pop()?.toLowerCase();
  if (BLOCKED_EXTENSIONS.has(ext)) return 'invalidFileType';
  if (file.size > MAX_FILE_SIZE) return 'fileTooLarge';
  return null;
}

// ─── Types ────────────────────────────────────────────

export interface DuplicateAlertState {
  file: File;
  existing: Resource;
  remainingDuplicates: number;
  resolve: (decision: { action: 'use-existing' | 'keep-both' | 'cancel'; applyToAll: boolean }) => void;
}

interface UseResourceUploadOptions {
  scopeType: 'personal' | 'team';
  scopeId: string;
  selectedFolderId: string | null | undefined;
  selectedLibraryId: string | null | undefined;
  setResources: React.Dispatch<React.SetStateAction<ResourceItem[]>>;
  /** Re-fetch the current resource list honouring active filter params. */
  reloadResources: () => Promise<void>;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

// ─── Hook ─────────────────────────────────────────────

export function useResourceUpload({
  scopeType,
  scopeId,
  selectedFolderId,
  selectedLibraryId,
  setResources,
  reloadResources,
  addToast,
}: UseResourceUploadOptions) {
  const { t } = useTranslation();
  const upload = useUpload();
  const uploading = upload.isUploading;

  const [dragOver, setDragOver] = useState(false);
  const [duplicateAlert, setDuplicateAlert] = useState<DuplicateAlertState | null>(null);
  const dragCounterRef = useRef(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const handleUpload = useCallback(async (files: FileList | File[]) => {
    if (!files.length || uploading) return;

    const validFiles: File[] = [];
    const initialProgress: UploadFileProgress[] = [];

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const validationError = validateFile(file);
      const id = `${Date.now()}-${i}`;

      if (validationError) {
        initialProgress.push({
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
        initialProgress.push({
          id,
          filename: file.name,
          percent: 0,
          status: 'uploading',
          fileSize: file.size,
          bytesUploaded: 0,
          speed: 0,
        });
      }
    }

    if (validFiles.length === 0 && initialProgress.length > 0) {
      upload.setItems(initialProgress);
      return;
    }

    upload.setItems((prev) => [...prev, ...initialProgress]);
    upload.setIsUploading(true);
    upload.setOverallProgress(0);

    const batchStartTime = Date.now();
    upload.setUploadStartTime(batchStartTime);

    let completedCount = 0;
    let linkedCount = 0;
    const validFileEntryIds = initialProgress
      .filter((p) => p.status === 'uploading')
      .map((p) => p.id);

    let batchDupAction: 'use-existing' | 'keep-both' | null = null;

    for (let i = 0; i < validFiles.length; i++) {
      const file = validFiles[i];
      const entryId = validFileEntryIds[i];
      const fileStartTime = Date.now();

      try {
        upload.setItems((prev) =>
          prev.map((p) => p.id === entryId ? { ...p, percent: 0 } : p)
        );

        const fileHash = await computeFileHash(file);
        const dupResult = await checkDuplicate(fileHash, file.size);

        if (dupResult.duplicate && dupResult.existing) {
          let action = batchDupAction;

          if (!action) {
            const remainingToCheck = validFiles.length - i - 1;
            const decision = await new Promise<{ action: 'use-existing' | 'keep-both' | 'cancel'; applyToAll: boolean }>((resolve) => {
              setDuplicateAlert({
                file,
                existing: dupResult.existing as Resource,
                remainingDuplicates: remainingToCheck,
                resolve,
              });
            });
            setDuplicateAlert(null);
            action = decision.action;
            if (decision.applyToAll) {
              batchDupAction = decision.action === 'cancel' ? null : decision.action;
            }
          }

          if (action === 'cancel') {
            upload.setItems((prev) =>
              prev.map((p) => p.id === entryId ? { ...p, status: 'error', error: t('common.cancel') } : p)
            );
            continue;
          }

          if (action === 'use-existing') {
            await linkExistingResource(
              String(dupResult.existing.id),
              scopeType,
              scopeId,
              selectedFolderId,
              selectedLibraryId,
            );
            linkedCount++;
            completedCount++;
            const fileSz = file.size;
            upload.setItems((prev) =>
              prev.map((p) => p.id === entryId
                ? { ...p, percent: 100, status: 'complete', bytesUploaded: fileSz, speed: 0 }
                : p
              )
            );
            upload.setOverallProgress(Math.round((completedCount / validFiles.length) * 100));
            continue;
          }
          // action === 'keep-both' → fall through to normal upload
        }

        await uploadResource(
          file,
          scopeType,
          scopeId,
          selectedFolderId,
          (progress) => {
            const fileEntry = initialProgress.find((p) => p.id === entryId);
            const fileSz = fileEntry?.fileSize || 0;
            const bytesUploaded = Math.round(fileSz * progress / 100);
            const elapsedSec = Math.max((Date.now() - fileStartTime) / 1000, 0.5);
            const speed = bytesUploaded > 0 ? Math.round(bytesUploaded / elapsedSec) : 0;
            upload.setItems((prev) =>
              prev.map((p) => p.id === entryId ? { ...p, percent: progress, bytesUploaded, speed } : p)
            );
            const overall = Math.round(((completedCount + progress / 100) / validFiles.length) * 100);
            upload.setOverallProgress(overall);
          },
          selectedLibraryId,
        );

        completedCount++;
        const fileEntry = initialProgress.find((p) => p.id === entryId);
        const fileSz = fileEntry?.fileSize || 0;
        upload.setItems((prev) =>
          prev.map((p) => p.id === entryId ? { ...p, percent: 100, status: 'complete', bytesUploaded: fileSz, speed: 0 } : p)
        );
      } catch {
        upload.setItems((prev) =>
          prev.map((p) => p.id === entryId
            ? { ...p, status: 'error', error: t('resources.uploadFailed') }
            : p
          )
        );
      }
    }

    if (linkedCount > 0) {
      addToast(
        linkedCount === 1
          ? t('resources.linkedExisting')
          : t('resources.linkedExistingCount', { count: linkedCount }),
        'success'
      );
    }

    try {
      await reloadResources();
    } catch { /* ignore */ }

    upload.setIsUploading(false);
    upload.setOverallProgress(0);
  }, [scopeType, scopeId, selectedFolderId, selectedLibraryId, uploading, t, upload, addToast, reloadResources]);

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
    duplicateAlert,
    setDuplicateAlert,
    fileInputRef,
    folderInputRef,
    handleUpload,
    handleDragEnter,
    handleDragOver,
    handleDragLeave,
    handleDrop,
  };
}
