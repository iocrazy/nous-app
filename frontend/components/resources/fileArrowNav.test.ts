import { describe, expect, it } from 'vitest';

import { shouldSuppressFileArrowNav } from './fileArrowNav';

describe('shouldSuppressFileArrowNav', () => {
  it('suppresses file-level ← / → for galleries (GalleryViewer owns the keys)', () => {
    expect(shouldSuppressFileArrowNav('application/x-nous-gallery')).toBe(true);
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
