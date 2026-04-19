import React, { useCallback, useRef, useState } from 'react';
import { Upload, X, FileText, CheckCircle, AlertCircle } from 'lucide-react';
import { importScript, ImportedChapter } from '../../../services/scriptService';

interface ImportScriptDialogProps {
  open: boolean;
  onClose: () => void;
  scriptId: string;
  onImportComplete: (chapters: ImportedChapter[]) => void;
}

const ACCEPTED_EXTENSIONS = ['.txt', '.pdf', '.docx'];
const ACCEPTED_MIME = [
  'text/plain',
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
];

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ImportScriptDialog({
  open,
  onClose,
  scriptId,
  onImportComplete,
}: ImportScriptDialogProps) {
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<{ chapters: ImportedChapter[] } | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const resetState = useCallback(() => {
    setFile(null);
    setLoading(false);
    setError('');
    setResult(null);
    setDragOver(false);
  }, []);

  const handleClose = useCallback(() => {
    resetState();
    onClose();
  }, [resetState, onClose]);

  const validateFile = (f: File): string => {
    const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase();
    if (!ACCEPTED_EXTENSIONS.includes(ext)) {
      return `Unsupported file type. Please upload ${ACCEPTED_EXTENSIONS.join(', ')} files.`;
    }
    if (f.size > 50 * 1024 * 1024) {
      return 'File size must be under 50 MB.';
    }
    return '';
  };

  const selectFile = (f: File) => {
    const validationError = validateFile(f);
    if (validationError) {
      setError(validationError);
      return;
    }
    setFile(f);
    setError('');
    setResult(null);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (selected) selectFile(selected);
    // Reset input so the same file can be re-selected
    e.target.value = '';
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) selectFile(dropped);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setDragOver(false);
  }, []);

  const handleUpload = async () => {
    if (!file) return;
    setLoading(true);
    setError('');
    try {
      const data = await importScript(scriptId, file);
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleConfirm = () => {
    if (!result) return;
    onImportComplete(result.chapters);
    handleClose();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60" onClick={handleClose} />

      {/* Dialog */}
      <div className="relative z-10 w-full max-w-lg mx-4 bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-zinc-700 shrink-0">
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-blue-600/20">
            <Upload className="w-4 h-4 text-blue-400" />
          </div>
          <h2 className="flex-1 text-base font-semibold text-zinc-100">Import Script</h2>
          <button
            onClick={handleClose}
            className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-700 transition-colors"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* Drop zone */}
          {!result && (
            <div
              className={`relative rounded-lg border-2 border-dashed transition-colors cursor-pointer ${
                dragOver
                  ? 'border-blue-500 bg-blue-500/10'
                  : 'border-zinc-600 bg-zinc-800/50 hover:border-zinc-500 hover:bg-zinc-800'
              }`}
              onDrop={handleDrop}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onClick={() => inputRef.current?.click()}
            >
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPTED_MIME.join(',')}
                className="hidden"
                onChange={handleFileChange}
              />
              <div className="flex flex-col items-center gap-3 px-6 py-8 text-center">
                <div className="flex items-center justify-center w-12 h-12 rounded-full bg-zinc-700">
                  <FileText className="w-6 h-6 text-zinc-300" />
                </div>
                {file ? (
                  <>
                    <p className="text-sm font-medium text-zinc-100 break-all">{file.name}</p>
                    <p className="text-xs text-zinc-400">{formatBytes(file.size)}</p>
                  </>
                ) : (
                  <>
                    <p className="text-sm text-zinc-300">
                      Drop a file here, or{' '}
                      <span className="text-blue-400 hover:text-blue-300">choose file</span>
                    </p>
                    <p className="text-xs text-zinc-500">Supports .txt, .pdf, .docx (max 50 MB)</p>
                  </>
                )}
              </div>
            </div>
          )}

          {/* Loading progress bar */}
          {loading && (
            <div className="space-y-2">
              <p className="text-xs text-zinc-400">Uploading and parsing…</p>
              <div className="h-1.5 w-full bg-zinc-700 rounded-full overflow-hidden">
                <div className="h-full bg-blue-500 rounded-full animate-pulse w-3/4" />
              </div>
            </div>
          )}

          {/* Error message */}
          {error && (
            <div className="flex items-start gap-2 rounded-lg bg-red-500/10 border border-red-500/30 px-3 py-2.5">
              <AlertCircle className="w-4 h-4 text-red-400 mt-0.5 shrink-0" />
              <p className="text-sm text-red-400">{error}</p>
            </div>
          )}

          {/* Result preview */}
          {result && (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <CheckCircle className="w-4 h-4 text-green-400 shrink-0" />
                <p className="text-sm font-medium text-green-400">
                  Parsed {result.chapters.length} chapter{result.chapters.length !== 1 ? 's' : ''}
                </p>
              </div>
              <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
                {result.chapters.map((ch, idx) => (
                  <div
                    key={idx}
                    className="flex gap-3 px-3 py-2.5 rounded-lg bg-zinc-800 border border-zinc-700"
                  >
                    <span className="shrink-0 text-xs font-mono text-zinc-500 mt-0.5 w-5 text-right">
                      {idx + 1}
                    </span>
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-zinc-100 truncate">
                        {ch.title || '(Untitled)'}
                      </p>
                      {ch.summary && (
                        <p className="text-xs text-zinc-400 mt-0.5 line-clamp-2">{ch.summary}</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-zinc-700 shrink-0">
          <button
            onClick={handleClose}
            className="px-3.5 py-2 text-sm rounded-lg text-zinc-300 hover:text-zinc-100 hover:bg-zinc-700 transition-colors"
          >
            Cancel
          </button>
          {!result ? (
            <button
              onClick={handleUpload}
              disabled={!file || loading}
              className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white font-medium hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? 'Importing…' : 'Upload'}
            </button>
          ) : (
            <button
              onClick={handleConfirm}
              className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white font-medium hover:bg-blue-500 transition-colors"
            >
              Confirm Import
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
