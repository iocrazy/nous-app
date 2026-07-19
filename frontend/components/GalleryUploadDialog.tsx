import React, { useState } from 'react';
import { X, Loader2, Images } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  createGallery,
  setGalleryItems,
  uploadResource,
} from '../services/resourceService';

interface GalleryUploadDialogProps {
  isOpen: boolean;
  onClose: () => void;
  scopeId: string;
  folderId?: string | null;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
  /** Called after the gallery + children are created so the list can reload. */
  onCreated: () => void | Promise<void>;
}

/** Max concurrent child-image uploads — keeps the request fan-out bounded. */
const UPLOAD_CONCURRENCY = 3;

/**
 * Upload-a-gallery dialog: pick a name + multiple images (selection order is
 * preserved as the gallery order), then create the gallery entity, upload each
 * image into the scope, and attach them as ordered children.
 */
export const GalleryUploadDialog: React.FC<GalleryUploadDialogProps> = ({
  isOpen,
  onClose,
  scopeId,
  folderId,
  addToast,
  onCreated,
}) => {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const reset = () => {
    setName('');
    setFiles([]);
    setProgress(0);
    setError(null);
  };

  const handleClose = () => {
    if (submitting) return;
    reset();
    onClose();
  };

  /** Upload the selected files, preserving order, with bounded concurrency.
   *  Returns resource ids in the SAME order as ``files`` (null = failed). */
  const uploadAll = async (): Promise<(string | null)[]> => {
    const results: (string | null)[] = new Array(files.length).fill(null);
    let done = 0;
    let next = 0;

    const worker = async () => {
      while (next < files.length) {
        const idx = next;
        next += 1;
        try {
          const resource = await uploadResource(files[idx], scopeId, folderId);
          results[idx] = String(resource.id);
        } catch (err) {
          console.error('gallery: child upload failed', err);
          results[idx] = null;
        } finally {
          done += 1;
          setProgress(Math.round((done / files.length) * 100));
        }
      }
    };

    const pool = Array.from(
      { length: Math.min(UPLOAD_CONCURRENCY, files.length) },
      () => worker(),
    );
    await Promise.all(pool);
    return results;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || files.length === 0 || submitting) return;

    setSubmitting(true);
    setError(null);
    setProgress(0);
    try {
      const gallery = await createGallery(scopeId, trimmed, folderId);
      const uploaded = await uploadAll();
      const imageIds = uploaded.filter((id): id is string => id !== null);
      const failedCount = uploaded.length - imageIds.length;

      if (imageIds.length === 0) {
        throw new Error(t('resources.galleryUploadAllFailed', 'All images failed to upload'));
      }

      await setGalleryItems(String(gallery.id), scopeId, imageIds);

      if (failedCount > 0) {
        addToast(
          t('resources.galleryCreatedPartial', {
            defaultValue: 'Gallery created ({{count}} image(s) failed to upload)',
            count: failedCount,
          }),
          'info',
        );
      } else {
        addToast(t('resources.galleryCreated', 'Gallery created'), 'success');
      }
      reset();
      onClose();
      await onCreated();
    } catch (err) {
      console.error('gallery: create failed', err);
      const msg = err instanceof Error && err.message
        ? err.message
        : t('resources.galleryCreateFailed', 'Failed to create gallery');
      setError(msg);
      addToast(msg, 'error');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={handleClose} />

      <div className="relative bg-ink-900 border border-ink-800 rounded-2xl w-full max-w-md mx-4 shadow-2xl animate-in zoom-in-95 fade-in duration-200">
        <div className="flex items-center justify-between p-5 border-b border-ink-800">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-ink-50">
            <Images size={18} className="text-pink-400" />
            {t('resources.uploadGallery', 'Upload Gallery')}
          </h2>
          <button
            onClick={handleClose}
            disabled={submitting}
            className="p-2 rounded-lg text-ink-400 hover:text-ink-50 hover:bg-ink-800 transition-colors disabled:opacity-50"
          >
            <X size={20} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          {error && (
            <div className="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
              {error}
            </div>
          )}

          <div className="space-y-2">
            <label className="text-sm font-medium text-ink-400">
              {t('resources.galleryName', 'Gallery Name')}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('resources.galleryNamePlaceholder', 'Enter gallery name') || ''}
              className="w-full bg-ink-800 border border-ink-700 rounded-lg px-4 py-2.5 text-ink-50 focus:border-indigo-500 outline-none transition-colors"
              autoFocus
              disabled={submitting}
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium text-ink-400">
              {t('resources.galleryImages', 'Images')}
            </label>
            <input
              type="file"
              accept="image/*"
              multiple
              disabled={submitting}
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
              className="w-full text-sm text-ink-300 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:bg-ink-800 file:text-ink-200 hover:file:bg-ink-700 file:cursor-pointer"
            />
            {files.length > 0 && (
              <p className="text-xs text-ink-500">
                {t('resources.gallerySelectedCount', {
                  defaultValue: '{{count}} image(s) selected',
                  count: files.length,
                })}
              </p>
            )}
          </div>

          {submitting && (
            <div className="space-y-1">
              <div className="h-1.5 bg-ink-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-indigo-500 transition-[width] duration-200"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <p className="text-xs text-ink-500">{progress}%</p>
            </div>
          )}

          <button
            type="submit"
            disabled={!name.trim() || files.length === 0 || submitting}
            className="w-full py-3 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg font-medium transition-colors flex items-center justify-center gap-2"
          >
            {submitting ? (
              <>
                <Loader2 className="animate-spin" size={18} />
                {t('resources.galleryCreating', 'Creating gallery...')}
              </>
            ) : (
              t('resources.createGallery', 'Create Gallery')
            )}
          </button>
        </form>
      </div>
    </div>
  );
};
