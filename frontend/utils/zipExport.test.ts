import { describe, expect, it } from 'vitest';

import type { Video } from '../types';
import { downloadKind, resolveExport } from './zipExport';

describe('downloadKind', () => {
  it('audio', () => {
    expect(downloadKind('audio')).toBe('audio');
  });
  it('gallery types', () => {
    for (const m of ['carousel', 'image_text', 'album', 'gallery', '2', '68']) {
      expect(downloadKind(m)).toBe('gallery');
    }
  });
  it('video default (and unknown/empty)', () => {
    for (const m of ['video', 'short', 'live_clip', '0', '4', '61', '', undefined, null]) {
      expect(downloadKind(m as string | undefined)).toBe('video');
    }
  });
});

describe('resolveExport', () => {
  const mk = (over: Partial<Video>): Video =>
    ({ platform_id: 'p1', title: 'My Song', media_type: 'audio', ...over } as Video);

  it('audio -> /music + .mp3', () => {
    const r = resolveExport(mk({}));
    expect(r.url).toContain('/media/download/p1/music');
    expect(r.filename).toBe('My Song.mp3');
  });
  it('video -> /download + .mp4', () => {
    const r = resolveExport(mk({ media_type: 'video' }));
    expect(r.url.endsWith('/media/download/p1')).toBe(true);
    expect(r.filename).toBe('My Song.mp4');
  });
  it('gallery -> /gallery + .zip', () => {
    const r = resolveExport(mk({ media_type: 'image_text' }));
    expect(r.url).toContain('/media/download/p1/gallery');
    expect(r.filename).toBe('My Song.zip');
  });
  it('sanitizes illegal chars, keeps spaces', () => {
    const r = resolveExport(mk({ title: 'a/b:c*?', media_type: 'audio' }));
    expect(r.filename).toBe('a_b_c__.mp3');
  });
});
