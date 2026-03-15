import { useCallback } from 'react';
import { useStoryboardStore, ImagePoolEntry } from '../../stores/storyboardStore';

// ─── Hook ─────────────────────────────────────────────────────────────────────

/**
 * Provides reference-counted access to the image pool stored in the
 * storyboard Zustand store. Use addRef/releaseRef instead of manipulating
 * the pool directly so images are only removed when no longer referenced.
 */
export function useImagePool() {
  const { imagePool, addToPool, removeFromPool } = useStoryboardStore();

  /**
   * Add an image to the pool or increment its reference count.
   * @param key   Unique identifier (e.g. image URL or hash)
   * @param entry Pool entry data — only used when creating a new entry
   */
  const addRef = useCallback(
    (key: string, entry: Omit<ImagePoolEntry, 'refCount'>) => {
      const existing = imagePool[key];
      if (existing) {
        addToPool(key, { ...existing, refCount: existing.refCount + 1 });
      } else {
        addToPool(key, { ...entry, refCount: 1 });
      }
    },
    [imagePool, addToPool]
  );

  /**
   * Decrement the reference count for an image.
   * Removes it from the pool when refCount reaches 0.
   */
  const releaseRef = useCallback(
    (key: string) => {
      const existing = imagePool[key];
      if (!existing) return;

      if (existing.refCount <= 1) {
        removeFromPool(key);
      } else {
        addToPool(key, { ...existing, refCount: existing.refCount - 1 });
      }
    },
    [imagePool, addToPool, removeFromPool]
  );

  /**
   * Returns the URL for a pooled image, or undefined if not found.
   */
  const getUrl = useCallback(
    (key: string): string | undefined => {
      return imagePool[key]?.url;
    },
    [imagePool]
  );

  /**
   * Returns the preview URL for a pooled image, falling back to the
   * full URL if no preview is available.
   */
  const getPreviewUrl = useCallback(
    (key: string): string | undefined => {
      const entry = imagePool[key];
      return entry?.previewUrl ?? entry?.url;
    },
    [imagePool]
  );

  return {
    imagePool,
    addRef,
    releaseRef,
    getUrl,
    getPreviewUrl,
  };
}
