import { describe, it, expect } from 'vitest';
import { isVideoType, isAlbumType } from './awemeType';

// Regression for the douyin media_type=51 bug: a "翻唱"/cover post came back
// as an unknown numeric type that carried video_download_urls, but a fixed
// (0/4/61) allowlist made isVideoType(51) === false. The Download menu then
// only offered "Cover" with no "Fetch Video", and the backend skipped the
// video download entirely. isVideoType is now content-driven: anything that
// is not an album/image or audio is video-capable, mirroring the backend
// gate (download_strategies.py: video when media_type not in (2, 68)).
describe('isVideoType — content-driven (not a fixed allowlist)', () => {
  it('treats known numeric video types as video', () => {
    for (const t of [0, 4, 61, '0', '4', '61']) {
      expect(isVideoType(t)).toBe(true);
    }
  });

  it('treats unknown/new douyin numeric types as video (the 51 regression)', () => {
    for (const t of [51, '51', 5, 99]) {
      expect(isVideoType(t)).toBe(true);
    }
  });

  it('treats string video labels as video', () => {
    for (const t of ['video', 'special', 'short', 'live_clip']) {
      expect(isVideoType(t)).toBe(true);
    }
  });

  it('does NOT treat album/image types as video', () => {
    for (const t of [2, 68, '2', '68', 'carousel', 'image_text']) {
      expect(isVideoType(t)).toBe(false);
      expect(isAlbumType(t)).toBe(true);
    }
  });

  it('does NOT treat audio as video', () => {
    expect(isVideoType('audio')).toBe(false);
  });

  it('returns false for null/undefined', () => {
    expect(isVideoType(undefined)).toBe(false);
    expect(isVideoType(null as unknown as undefined)).toBe(false);
  });
});
