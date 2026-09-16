import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import {
  resumeKeyFor,
  savePosition,
  loadPosition,
  clearPosition,
  clearUser,
  scopedKey,
  ANONYMOUS_USER,
  prune,
  MAX_AGE_MS,
  MAX_ENTRIES,
  MIN_RESUME_SECONDS,
  END_MARGIN_SECONDS,
} from './playbackResume';

const U = 'user-alice';
const V = 'user-bob';

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
    savePosition(U, 'k', 90, 600);
    expect(loadPosition(U, 'k')).toBe(90);
  });

  it('does not remember the first few seconds', () => {
    savePosition(U, 'k', MIN_RESUME_SECONDS - 1, 600);
    expect(loadPosition(U, 'k')).toBeNull();
  });

  it('does not remember a position inside the end margin', () => {
    savePosition(U, 'k', 600 - (END_MARGIN_SECONDS - 1), 600);
    expect(loadPosition(U, 'k')).toBeNull();
  });

  it('watching to the end CLEARS an earlier position', () => {
    savePosition(U, 'k', 300, 600);
    expect(loadPosition(U, 'k')).toBe(300);
    savePosition(U, 'k', 599, 600); // played through
    expect(loadPosition(U, 'k')).toBeNull();
  });

  it('re-checks the end margin against the live duration', () => {
    // Stored when the player thought the clip was 600s; it is really 95s, so
    // 90s is now inside the end margin and must not resume.
    savePosition(U, 'k', 90, 600);
    expect(loadPosition(U, 'k', 95)).toBeNull();
    expect(loadPosition(U, 'k', 600)).toBe(90);
  });

  it('clearPosition removes only its own key', () => {
    savePosition(U, 'a', 90, 600);
    savePosition(U, 'b', 90, 600);
    clearPosition(U, 'a');
    expect(loadPosition(U, 'a')).toBeNull();
    expect(loadPosition(U, 'b')).toBe(90);
  });

  it('returns null for unknown keys and for an empty key', () => {
    expect(loadPosition(U, 'nope')).toBeNull();
    expect(loadPosition(U, '')).toBeNull();
  });
});

describe('storage failures degrade to "no memory"', () => {
  afterEach(() => vi.restoreAllMocks());

  it('a throwing getItem does not propagate', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked');
    });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(loadPosition(U, 'k')).toBeNull();
  });

  it('a quota error on setItem does not propagate', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError');
    });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => savePosition(U, 'k', 90, 600)).not.toThrow();
  });

  it('corrupt JSON reads as empty instead of crashing the player', () => {
    localStorage.setItem('mediahub_playback_positions_v1', '{not json');
    vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(loadPosition(U, 'k')).toBeNull();
  });

  it('a non-object payload reads as empty', () => {
    localStorage.setItem('mediahub_playback_positions_v1', '[1,2,3]');
    expect(loadPosition(U, 'k')).toBeNull();
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


describe('per-user scoping', () => {
  it('two accounts on one browser do not see each other\'s position', () => {
    // The real shape: a TEAM resource, so both accounts legitimately open the
    // very same id. Without the namespace Bob would resume where Alice left.
    savePosition(U, 'resource:777', 300, 600);
    expect(loadPosition(V, 'resource:777', 600)).toBeNull();
    expect(loadPosition(U, 'resource:777', 600)).toBe(300);
  });

  it('one account overwriting does not touch the other', () => {
    savePosition(U, 'resource:777', 300, 600);
    savePosition(V, 'resource:777', 120, 600);
    expect(loadPosition(U, 'resource:777', 600)).toBe(300);
    expect(loadPosition(V, 'resource:777', 600)).toBe(120);
  });

  it('clearPosition stays inside its own namespace', () => {
    savePosition(U, 'resource:777', 300, 600);
    savePosition(V, 'resource:777', 120, 600);
    clearPosition(V, 'resource:777');
    expect(loadPosition(U, 'resource:777', 600)).toBe(300);
    expect(loadPosition(V, 'resource:777', 600)).toBeNull();
  });

  it('signed-out viewing gets its own namespace, not a shared one', () => {
    savePosition(null, 'resource:777', 300, 600);
    expect(loadPosition(U, 'resource:777', 600)).toBeNull();
    expect(loadPosition(null, 'resource:777', 600)).toBe(300);
    // null / undefined / blank all mean the same viewer
    expect(loadPosition(undefined, 'resource:777', 600)).toBe(300);
    expect(scopedKey('  ', 'x')).toBe(scopedKey(null, 'x'));
    expect(scopedKey(null, 'x')).toBe(`${ANONYMOUS_USER}::x`);
  });
});

describe('clearUser', () => {
  it('drops only the signing-out account\'s entries', () => {
    savePosition(U, 'a', 300, 600);
    savePosition(U, 'b', 300, 600);
    savePosition(V, 'a', 120, 600);

    clearUser(U);

    expect(loadPosition(U, 'a', 600)).toBeNull();
    expect(loadPosition(U, 'b', 600)).toBeNull();
    expect(loadPosition(V, 'a', 600)).toBe(120);
  });

  it('is a no-op when the account has nothing stored', () => {
    savePosition(V, 'a', 120, 600);
    const before = localStorage.getItem('mediahub_playback_positions_v1');
    clearUser(U);
    expect(localStorage.getItem('mediahub_playback_positions_v1')).toBe(before);
  });

  it('a user id that is a prefix of another does not take its entries', () => {
    // 'user-1' must not sweep 'user-10' — the separator is what prevents it.
    savePosition('user-1', 'a', 300, 600);
    savePosition('user-10', 'a', 120, 600);
    clearUser('user-1');
    expect(loadPosition('user-1', 'a', 600)).toBeNull();
    expect(loadPosition('user-10', 'a', 600)).toBe(120);
  });
});
