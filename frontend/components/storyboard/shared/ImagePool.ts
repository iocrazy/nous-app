import { useStoryboardStore, ImagePoolEntry } from '../../../stores/storyboardStore';

// ─── ImagePool ────────────────────────────────────────────────────────────────
//
// Utility class for reference-counted image caching.
// Images are kept in the Zustand store under `imagePool`.
// When refCount reaches zero the entry is removed.

export class ImagePool {
  /**
   * Increment the ref count for a key.
   * If the key is not in the pool yet, a new entry is created.
   */
  static addRef(key: string, url: string, previewUrl?: string): void {
    const { imagePool, addToPool } = useStoryboardStore.getState();
    const existing = imagePool[key];

    if (existing) {
      addToPool(key, { ...existing, refCount: existing.refCount + 1 });
    } else {
      addToPool(key, { url, previewUrl, refCount: 1 });
    }
  }

  /**
   * Decrement the ref count for a key.
   * If the count reaches zero the entry is removed from the pool.
   */
  static releaseRef(key: string): void {
    const { imagePool, addToPool, removeFromPool } = useStoryboardStore.getState();
    const existing = imagePool[key];
    if (!existing) return;

    if (existing.refCount <= 1) {
      removeFromPool(key);
    } else {
      addToPool(key, { ...existing, refCount: existing.refCount - 1 });
    }
  }

  /**
   * Get the full-resolution URL for a key.
   * Returns undefined if the key is not in the pool.
   */
  static getUrl(key: string): string | undefined {
    return useStoryboardStore.getState().imagePool[key]?.url;
  }

  /**
   * Get the preview (thumbnail) URL for a key.
   * Falls back to the full URL if no preview is registered.
   */
  static getPreviewUrl(key: string): string | undefined {
    const entry: ImagePoolEntry | undefined = useStoryboardStore.getState().imagePool[key];
    if (!entry) return undefined;
    return entry.previewUrl ?? entry.url;
  }

  /**
   * Return the current ref count for a key, or 0 if not pooled.
   */
  static getRefCount(key: string): number {
    return useStoryboardStore.getState().imagePool[key]?.refCount ?? 0;
  }

  /**
   * Check whether a key is currently in the pool.
   */
  static has(key: string): boolean {
    return key in useStoryboardStore.getState().imagePool;
  }

  /**
   * Get a snapshot of all pooled entries (read-only).
   */
  static getAll(): Readonly<Record<string, ImagePoolEntry>> {
    return useStoryboardStore.getState().imagePool;
  }
}

export default ImagePool;
