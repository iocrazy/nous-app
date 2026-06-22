import React, { createContext, useCallback, useContext, useState } from 'react';

import type { Video } from '../types';
import { exportMediaAsZip } from '../utils/zipExport';

// Client-side download/zip-export tasks surfaced in the Task Center (like
// uploads) — so a bulk export shows live progress + stays as a record instead
// of just a fleeting toast. In-memory for the session (mirrors UploadContext).

export interface ExportTask {
  id: string;
  label: string;
  percent: number;
  done: number;
  total: number;
  failed: number;
  status: 'running' | 'complete' | 'error';
  error?: string;
}

interface ExportTaskContextType {
  items: ExportTask[];
  /** Zip the given media in the browser, tracked as a Task Center entry. */
  runZipExport: (videos: Video[], label?: string) => Promise<void>;
  clearFinished: () => void;
}

const ExportTaskContext = createContext<ExportTaskContextType | null>(null);

let _seq = 0;

export const ExportTaskProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [items, setItems] = useState<ExportTask[]>([]);

  const runZipExport = useCallback(async (videos: Video[], label?: string) => {
    if (!videos.length) return;
    const id = `export-${Date.now()}-${_seq++}`;
    const total = videos.length;
    const patch = (p: Partial<ExportTask>) =>
      setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...p } : it)));

    setItems((prev) => [
      {
        id,
        label: label || `${total} item${total > 1 ? 's' : ''} · zip`,
        percent: 0,
        done: 0,
        total,
        failed: 0,
        status: 'running',
      },
      ...prev,
    ]);

    try {
      const { ok, failed } = await exportMediaAsZip(videos, {
        onProgress: (pr) =>
          patch({
            done: pr.done,
            failed: pr.failed,
            percent: pr.total ? Math.round((pr.done / pr.total) * 100) : 0,
          }),
      });
      patch({
        status: ok === 0 ? 'error' : 'complete',
        percent: 100,
        done: ok,
        failed,
        error: ok === 0 ? 'No files could be fetched' : undefined,
      });
    } catch (e) {
      patch({ status: 'error', error: e instanceof Error ? e.message : 'Export failed' });
    }
  }, []);

  const clearFinished = useCallback(() => {
    setItems((prev) => prev.filter((it) => it.status === 'running'));
  }, []);

  return (
    <ExportTaskContext.Provider value={{ items, runZipExport, clearFinished }}>
      {children}
    </ExportTaskContext.Provider>
  );
};

export function useExportTasks(): ExportTaskContextType {
  const ctx = useContext(ExportTaskContext);
  if (!ctx) throw new Error('useExportTasks must be used within ExportTaskProvider');
  return ctx;
}
