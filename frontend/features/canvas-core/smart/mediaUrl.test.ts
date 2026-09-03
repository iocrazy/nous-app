import { describe, expect, it, vi } from 'vitest';

vi.mock('../../../utils/apiConfig', () => ({
  getApiUrl: () => 'https://api.example.com',
}));

import { fullResPath, fullResSrc, mediaSrc } from './mediaUrl';

describe('mediaSrc', () => {
  it('prefixes relative generated-media urls with the API base', () => {
    // `/file` (not `/cover`) so this stays a pure base-prefixing pin — the
    // cover cache-bust has its own describe below.
    expect(mediaSrc('/api/v1/generated-media/1/file')).toBe(
      'https://api.example.com/api/v1/generated-media/1/file',
    );
  });
  it('leaves absolute and blob urls alone', () => {
    expect(mediaSrc('https://cdn/x.png')).toBe('https://cdn/x.png');
    expect(mediaSrc('blob:xyz')).toBe('blob:xyz');
  });
  it('empty-safe', () => {
    expect(mediaSrc(null)).toBe('');
    expect(mediaSrc(undefined)).toBe('');
  });
});

// ---------------------------------------------------------------------------
// Preview tier (canvas fluency W1/W2). `/cover` now answers with a 1024px
// WebP by default and only hands back the original for `?full=1`. Two
// consequences pinned here:
//   1. Browsers hold the OLD `/cover` bytes (the original) under a 7-day
//      immutable cache, so every preview consumer must ask a different URL
//      once — hence the `v=2` marker.
//   2. Consumers that need real pixels (lightbox, editors, brush, export,
//      download) go through `fullResSrc` / `fullResPath` instead.
// ---------------------------------------------------------------------------

describe('mediaSrc — preview cache bust', () => {
  it('busts the immutable cache on generated-media covers', () => {
    expect(mediaSrc('/api/v1/generated-media/1/cover')).toBe(
      'https://api.example.com/api/v1/generated-media/1/cover?v=2',
    );
  });

  it('touches nothing else: /file, absolute and empty urls', () => {
    expect(mediaSrc('/api/v1/generated-media/1/file')).toBe(
      'https://api.example.com/api/v1/generated-media/1/file',
    );
    expect(mediaSrc('/api/v1/generated-media/1/stream')).toBe(
      'https://api.example.com/api/v1/generated-media/1/stream',
    );
    expect(mediaSrc('https://cdn/x.png')).toBe('https://cdn/x.png');
    expect(mediaSrc('')).toBe('');
  });

  it('is idempotent — a already-busted url is not stamped twice', () => {
    const once = mediaSrc('/api/v1/generated-media/1/cover');
    expect(mediaSrc(once)).toBe(once);
  });
});

describe('fullResSrc', () => {
  it('asks /cover for the original explicitly', () => {
    expect(fullResSrc('/api/v1/generated-media/7/cover')).toBe(
      'https://api.example.com/api/v1/generated-media/7/cover?v=2&full=1',
    );
  });

  it('appends with & when a query already exists', () => {
    expect(fullResSrc('/api/v1/generated-media/7/cover?t=9')).toBe(
      'https://api.example.com/api/v1/generated-media/7/cover?t=9&v=2&full=1',
    );
  });

  it('spells the flag `full=1` — the server types it as int, `full=true` 422s', () => {
    expect(fullResSrc('/api/v1/generated-media/7/cover')).toMatch(/[?&]full=1$/);
  });

  it('leaves non-cover urls exactly as mediaSrc does', () => {
    for (const u of ['/api/v1/generated-media/7/file', 'https://x/y.png', '']) {
      expect(fullResSrc(u)).toBe(mediaSrc(u));
    }
  });

  it('is idempotent', () => {
    const once = fullResSrc('/api/v1/generated-media/7/cover');
    expect(fullResSrc(once)).toBe(once);
    expect(mediaSrc(once)).toBe(once);
  });
});

describe('fullResPath — relative-preserving, for apiFetch', () => {
  it('keeps the path relative so apiFetch can build its own base', () => {
    expect(fullResPath('/api/v1/generated-media/7/cover')).toBe(
      '/api/v1/generated-media/7/cover?v=2&full=1',
    );
  });

  it('passes non-cover paths through untouched', () => {
    expect(fullResPath('/api/v1/generated-media/7/file')).toBe(
      '/api/v1/generated-media/7/file',
    );
    expect(fullResPath('')).toBe('');
  });
});
