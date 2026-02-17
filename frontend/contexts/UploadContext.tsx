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

interface UploadContextType {
  items: UploadFileProgress[];
  isUploading: boolean;
  overallProgress: number;
  uploadStartTime: number;
  setItems: React.Dispatch<React.SetStateAction<UploadFileProgress[]>>;
  setIsUploading: (v: boolean) => void;
  setOverallProgress: (v: number) => void;
  setUploadStartTime: (v: number) => void;
  clearCompleted: () => void;
}

const UploadContext = createContext<UploadContextType | null>(null);

// ─── Provider ──────────────────────────────────────────

export const UploadProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [items, setItems] = useState<UploadFileProgress[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [overallProgress, setOverallProgress] = useState(0);
  const [uploadStartTime, setUploadStartTime] = useState(0);

  const clearCompleted = useCallback(() => {
    setItems([]);
    setIsUploading(false);
    setOverallProgress(0);
  }, []);

  return (
    <UploadContext.Provider value={{
      items,
      isUploading,
      overallProgress,
      uploadStartTime,
      setItems,
      setIsUploading,
      setOverallProgress,
      setUploadStartTime,
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
