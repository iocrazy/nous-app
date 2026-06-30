import React, { createContext, useContext, useState, useCallback } from 'react';

// ─── Types ─────────────────────────────────────────────

export interface UploadFileProgress {
  id: string;
  filename: string;
  percent: number;
  status: 'uploading' | 'complete' | 'error';
  error?: string;
  fileSize: number;
  bytesUploaded: number;
  speed: number;
}

/**
 * Aggregate progress summary for bulk imports.
 * Set by useResourceUpload; consumed by the TopBar task panel.
 * null when no bulk import is running.
 */
export interface BulkSummary {
  total: number;
  done: number;
  linked: number;
  failed: number;
  phase: 'hashing' | 'checking' | 'transferring' | 'idle';
}

interface UploadContextType {
  items: UploadFileProgress[];
  isUploading: boolean;
  overallProgress: number;
  uploadStartTime: number;
  /** Aggregate summary for bulk imports (null when idle). */
  bulkSummary: BulkSummary | null;
  setItems: React.Dispatch<React.SetStateAction<UploadFileProgress[]>>;
  setIsUploading: (v: boolean) => void;
  setOverallProgress: (v: number) => void;
  setUploadStartTime: (v: number) => void;
  setBulkSummary: React.Dispatch<React.SetStateAction<BulkSummary | null>>;
  clearCompleted: () => void;
}

const UploadContext = createContext<UploadContextType | null>(null);

// ─── Provider ──────────────────────────────────────────

export const UploadProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [items, setItems] = useState<UploadFileProgress[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [overallProgress, setOverallProgress] = useState(0);
  const [uploadStartTime, setUploadStartTime] = useState(0);
  const [bulkSummary, setBulkSummary] = useState<BulkSummary | null>(null);

  const clearCompleted = useCallback(() => {
    setItems([]);
    setIsUploading(false);
    setOverallProgress(0);
    setBulkSummary(null);
  }, []);

  return (
    <UploadContext.Provider value={{
      items,
      isUploading,
      overallProgress,
      uploadStartTime,
      bulkSummary,
      setItems,
      setIsUploading,
      setOverallProgress,
      setUploadStartTime,
      setBulkSummary,
      clearCompleted,
    }}>
      {children}
    </UploadContext.Provider>
  );
};

// ─── Hook ──────────────────────────────────────────────

export function useUpload(): UploadContextType {
  const ctx = useContext(UploadContext);
  if (!ctx) throw new Error('useUpload must be used within UploadProvider');
  return ctx;
}

// ─── Utilities ─────────────────────────────────────────

export function formatSpeed(bytesPerSec: number): string {
  if (bytesPerSec < 1024) return `${bytesPerSec} B/s`;
  if (bytesPerSec < 1048576) return `${(bytesPerSec / 1024).toFixed(1)} KB/s`;
  return `${(bytesPerSec / 1048576).toFixed(1)} MB/s`;
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(2)} MB`;
  return `${(bytes / 1073741824).toFixed(2)} GB`;
}

export function formatTimeRemaining(seconds: number): string {
  if (!seconds || !isFinite(seconds)) return '--:--';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export function getFileTypeIcon(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  const videoExts = ['mp4', 'mov', 'avi', 'mkv', 'webm', 'flv'];
  const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp'];
  const audioExts = ['mp3', 'wav', 'flac', 'aac', 'ogg', 'm4a'];
  const docExts = ['pdf', 'doc', 'docx', 'txt', 'md', 'rtf'];
  if (videoExts.includes(ext)) return 'video';
  if (imageExts.includes(ext)) return 'image';
  if (audioExts.includes(ext)) return 'audio';
  if (docExts.includes(ext)) return 'document';
  return 'other';
}
