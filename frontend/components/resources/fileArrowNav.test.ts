import { describe, expect, it, vi } from 'vitest';

// Keep the helper's import of resourceService cheap — we only need the mime
// constant, not the whole (supabase-pulling) service module.
vi.mock('../../services/resourceService', () => ({
  GALLERY_MIME: 'application/x-mediahub-gallery',
}));

import { shouldSuppressFileArrowNav } from './fileArrowNav';

describe('shouldSuppressFileArrowNav', () => {
  it('suppresses file-level ← / → for galleries (GalleryViewer owns the keys)', () => {
    expect(shouldSuppressFileArrowNav('application/x-mediahub-gallery')).toBe(true);
  });

  it('suppresses for video (VideoPlayer seeks with arrows)', () => {
    expect(shouldSuppressFileArrowNav('video/mp4')).toBe(true);
  });

  it('allows file nav for plain images / audio / documents', () => {
    expect(shouldSuppressFileArrowNav('image/jpeg')).toBe(false);
    expect(shouldSuppressFileArrowNav('audio/mpeg')).toBe(false);
    expect(shouldSuppressFileArrowNav('application/pdf')).toBe(false);
  });

  it('allows file nav when the mime type is unknown', () => {
    expect(shouldSuppressFileArrowNav(null)).toBe(false);
    expect(shouldSuppressFileArrowNav(undefined)).toBe(false);
    expect(shouldSuppressFileArrowNav('')).toBe(false);
  });
});
