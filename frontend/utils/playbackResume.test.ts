import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import {
  resumeKeyFor,
  savePosition,
  loadPosition,
  clearPosition,
  prune,
  MAX_AGE_MS,
  MAX_ENTRIES,
  MIN_RESUME_SECONDS,
  END_MARGIN_SECONDS,
} from './playbackResume';

beforeEach(() => {
  localStorage.clear();
});

describe('resumeKeyFor', () => {
  it('prefers the caller-supplied identity', () => {
    expect(resumeKeyFor('res-123', '/api/v1/resources/999/stream?token=abc')).toBe('res-123');
  });

  it('falls back to the path, so a rotated token does not orphan the position', () => {
    const a = resumeKeyFor(undefined, '/api/v1/resources/9/stream?token=OLD');
    const b = resumeKeyFor(undefined, '/api/v1/resources/9/stream?token=NEW');
    expect(a).toBe(b);
  });

  it('ignores a blank explicit key rather than storing under ""', () => {
    expect(resumeKeyFor('   ', '/media/7?token=x')).toBe('/media/7');
  });
});

describe('savePosition / loadPosition', () => {
  it('round-trips a mid-video position', () => {
    savePosition('k', 90, 600);
    expect(loadPosition('k')).toBe(90);
  });

  it('does not remember the first few seconds', () => {
    savePosition('k', MIN_RESUME_SECONDS - 1, 600);
    expect(loadPosition('k')).toBeNull();
  });

  it('does not remember a position inside the end margin', () => {
    savePosition('k', 600 - (END_MARGIN_SECONDS - 1), 600);
    expect(loadPosition('k')).toBeNull();
  });

  it('watching to the end CLEARS an earlier position', () => {
    savePosition('k', 300, 600);
    expect(loadPosition('k')).toBe(300);
    savePosition('k', 599, 600); // played through
    expect(loadPosition('k')).toBeNull();
  });

  it('re-checks the end margin against the live duration', () => {
    // Stored when the player thought the clip was 600s; it is really 95s, so
    // 90s is now inside the end margin and must not resume.
    savePosition('k', 90, 600);
    expect(loadPosition('k', 95)).toBeNull();
    expect(loadPosition('k', 600)).toBe(90);
  });

  it('clearPosition removes only its own key', () => {
    savePosition('a', 90, 600);
    savePosition('b', 90, 600);
    clearPosition('a');
    expect(loadPosition('a')).toBeNull();
    expect(loadPosition('b')).toBe(90);
  });

  it('returns null for unknown keys and for an empty key', () => {
    expect(loadPosition('nope')).toBeNull();
    expect(loadPosition('')).toBeNull();
  });
});

describe('storage failures degrade to "no memory"', () => {
  afterEach(() => vi.restoreAllMocks());

  it('a throwing getItem does not propagate', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked');
    });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(loadPosition('k')).toBeNull();
  });

  it('a quota error on setItem does not propagate', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError');
    });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => savePosition('k', 90, 600)).not.toThrow();
  });

  it('corrupt JSON reads as empty instead of crashing the player', () => {
    localStorage.setItem('mediahub_playback_positions_v1', '{not json');
    vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(loadPosition('k')).toBeNull();
  });

  it('a non-object payload reads as empty', () => {
    localStorage.setItem('mediahub_playback_positions_v1', '[1,2,3]');
    expect(loadPosition('k')).toBeNull();
  });
});

describe('prune', () => {
  it('drops entries past MAX_AGE_MS', () => {
    const now = 1_000_000_000_000;
    const out = prune(
      {
        fresh: { t: 1, d: 2, ts: now - 1000 },
        stale: { t: 1, d: 2, ts: now - MAX_AGE_MS - 1 },
      },
      now,
    );
    expect(Object.keys(out)).toEqual(['fresh']);
  });

  it('caps the store at MAX_ENTRIES, keeping the newest', () => {
    const now = 1_000_000_000_000;
    const store: Record<string, { t: number; d: number; ts: number }> = {};
    for (let i = 0; i < MAX_ENTRIES + 25; i++) {
      store[`k${i}`] = { t: 90, d: 600, ts: now - i * 1000 };
    }
    const out = prune(store, now);
    expect(Object.keys(out)).toHaveLength(MAX_ENTRIES);
    expect(out.k0).toBeDefined(); // newest kept
    expect(out[`k${MAX_ENTRIES + 24}`]).toBeUndefined(); // oldest dropped
  });

  it('survives a malformed entry instead of throwing', () => {
    const now = 1_000_000_000_000;
    const out = prune(
      { good: { t: 1, d: 2, ts: now }, bad: null as never },
      now,
    );
    expect(Object.keys(out)).toEqual(['good']);
  });
});
