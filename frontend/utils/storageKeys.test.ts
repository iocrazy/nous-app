import { readFileSync } from 'fs';
import path from 'path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  LEGACY_LOCAL_KEYS,
  LEGACY_SESSION_KEYS,
  MIGRATION_MARKER_KEY,
  STORAGE_KEYS,
  SESSION_KEYS,
  migrateLegacyStorageKeys,
  projectEpisodeKey,
  purgeLegacyStorageKeys,
  TODOLIST_ATTENTION_PREFIX,
  TODOLIST_COLUMNS_PREFIX,
} from './storageKeys';

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

const brokenStorage = (): Storage =>
  ({
    get length(): number {
      throw new Error('SecurityError');
    },
    key: () => {
      throw new Error('SecurityError');
    },
    getItem: () => {
      throw new Error('SecurityError');
    },
    setItem: () => {
      throw new Error('SecurityError');
    },
    removeItem: () => {
      throw new Error('SecurityError');
    },
    clear: () => {},
  }) as unknown as Storage;

describe('migrateLegacyStorageKeys (copy only — deletion is the purge\'s job)', () => {
  it('copies every exact legacy localStorage key and leaves the old one for the purge', () => {
    for (const oldKey of Object.keys(LEGACY_LOCAL_KEYS)) {
      localStorage.setItem(oldKey, `v:${oldKey}`);
    }
    migrateLegacyStorageKeys();
    for (const [oldKey, newKey] of Object.entries(LEGACY_LOCAL_KEYS)) {
      expect(localStorage.getItem(newKey)).toBe(`v:${oldKey}`);
      expect(localStorage.getItem(oldKey)).toBe(`v:${oldKey}`);
    }
  });

  it('copies the sessionStorage keys too, keeping the old ones', () => {
    for (const oldKey of Object.keys(LEGACY_SESSION_KEYS)) {
      sessionStorage.setItem(oldKey, '1');
    }
    migrateLegacyStorageKeys();
    for (const [oldKey, newKey] of Object.entries(LEGACY_SESSION_KEYS)) {
      expect(sessionStorage.getItem(newKey)).toBe('1');
      expect(sessionStorage.getItem(oldKey)).toBe('1');
    }
  });

  it('covers every key the app reads — no mediahub name is left unmapped', () => {
    const newNames = new Set(Object.values(LEGACY_LOCAL_KEYS));
    for (const k of Object.values(STORAGE_KEYS)) expect(newNames.has(k)).toBe(true);
    const newSession = new Set(Object.values(LEGACY_SESSION_KEYS));
    for (const k of Object.values(SESSION_KEYS)) expect(newSession.has(k)).toBe(true);
    for (const k of [...Object.values(STORAGE_KEYS), ...Object.values(SESSION_KEYS)]) {
      expect(k.toLowerCase()).not.toContain('mediahub');
    }
  });

  it('never overwrites a value already stored under the new key', () => {
    localStorage.setItem('mediahub_selected_team', 'old-team');
    localStorage.setItem(STORAGE_KEYS.selectedTeam, 'new-team');
    migrateLegacyStorageKeys();
    expect(localStorage.getItem(STORAGE_KEYS.selectedTeam)).toBe('new-team');
    expect(localStorage.getItem('mediahub_selected_team')).toBe('old-team');
  });

  it('is idempotent', () => {
    localStorage.setItem('mediahub.theme', 'light');
    migrateLegacyStorageKeys();
    const snapshot = JSON.stringify({ ...localStorage });
    migrateLegacyStorageKeys();
    expect(JSON.stringify({ ...localStorage })).toBe(snapshot);
    expect(localStorage.getItem(STORAGE_KEYS.theme)).toBe('light');
  });

  it('runs once: a new key the app deliberately removed is NOT resurrected from the kept old key', () => {
    // useTeams drops an invalid selected team; LoginPage consumes auth_expired.
    localStorage.setItem('mediahub_selected_team', 'stale-team');
    sessionStorage.setItem('mediahub_auth_expired', '1');
    migrateLegacyStorageKeys();
    localStorage.removeItem(STORAGE_KEYS.selectedTeam);
    sessionStorage.removeItem(SESSION_KEYS.authExpired);
    migrateLegacyStorageKeys();
    expect(localStorage.getItem(STORAGE_KEYS.selectedTeam)).toBeNull();
    expect(sessionStorage.getItem(SESSION_KEYS.authExpired)).toBeNull();
    expect(localStorage.getItem(MIGRATION_MARKER_KEY)).not.toBeNull();
  });

  it('copies the prefix families (per-project episode, todolist columns / attention)', () => {
    localStorage.setItem('mediahub.project.p1.ep', '2');
    localStorage.setItem('mediahub:todolist:columns:team:7', '["id"]');
    localStorage.setItem('mediahub:todolist:attention:me', '1');
    localStorage.setItem('unrelated', 'keep');
    migrateLegacyStorageKeys();
    expect(localStorage.getItem(projectEpisodeKey('p1'))).toBe('2');
    expect(localStorage.getItem(`${TODOLIST_COLUMNS_PREFIX}:team:7`)).toBe('["id"]');
    expect(localStorage.getItem(`${TODOLIST_ATTENTION_PREFIX}:me`)).toBe('1');
    expect(localStorage.getItem('mediahub.project.p1.ep')).toBe('2');
    expect(localStorage.getItem('mediahub:todolist:columns:team:7')).toBe('["id"]');
    expect(localStorage.getItem('mediahub:todolist:attention:me')).toBe('1');
    expect(localStorage.getItem('unrelated')).toBe('keep');
  });

  it('does not overwrite an existing prefixed value either', () => {
    localStorage.setItem('mediahub.project.p1.ep', '1');
    localStorage.setItem(projectEpisodeKey('p1'), '3');
    migrateLegacyStorageKeys();
    expect(localStorage.getItem(projectEpisodeKey('p1'))).toBe('3');
  });

  it('never throws when storage throws, and logs instead', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    const broken = brokenStorage();
    expect(() => migrateLegacyStorageKeys({ local: broken, session: broken })).not.toThrow();
    expect(err).toHaveBeenCalled();
  });

  it('a failed write (quota) keeps the old value, migrates the rest, and retries on the next boot', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    localStorage.setItem('mediahub.theme', 'light');
    localStorage.setItem('mediahub_selected_team', 'team-9');
    const real = localStorage.setItem.bind(localStorage);
    const spy = vi.spyOn(localStorage, 'setItem').mockImplementation((k: string, v: string) => {
      if (k === STORAGE_KEYS.theme) throw new Error('QuotaExceededError');
      return real(k, v);
    });
    migrateLegacyStorageKeys();
    expect(localStorage.getItem(STORAGE_KEYS.selectedTeam)).toBe('team-9');
    expect(localStorage.getItem('mediahub.theme')).toBe('light');
    // Not marked done, so the next boot tries again…
    expect(localStorage.getItem(MIGRATION_MARKER_KEY)).toBeNull();
    spy.mockRestore();
    migrateLegacyStorageKeys();
    expect(localStorage.getItem(STORAGE_KEYS.theme)).toBe('light');
    expect(localStorage.getItem(MIGRATION_MARKER_KEY)).not.toBeNull();
  });
});

describe('purgeLegacyStorageKeys', () => {
  it('skips a storage whose migration marker is absent (migration never finished)', () => {
    localStorage.setItem('mediahub.theme', 'light');
    localStorage.setItem(STORAGE_KEYS.theme, 'light');
    sessionStorage.setItem('mediahub_error_session_id', 'abc');
    sessionStorage.setItem(SESSION_KEYS.errorSessionId, 'abc');

    purgeLegacyStorageKeys();

    // Both new keys hold values, so only the marker guard can have kept these.
    expect(localStorage.getItem('mediahub.theme')).toBe('light');
    expect(sessionStorage.getItem('mediahub_error_session_id')).toBe('abc');
  });

  it('removes only legacy keys, and only those whose new key exists', () => {
    localStorage.setItem('mediahub.theme', 'light');
    localStorage.setItem('mediahub_selected_team', 'team-1');
    localStorage.setItem('mediahub.project.p1.ep', '2');
    sessionStorage.setItem('mediahub_error_session_id', 'abc');
    migrateLegacyStorageKeys();
    // This one has no new counterpart (e.g. a failed copy) — must survive purge.
    localStorage.setItem('mediahub_volume_pref', '{"volume":0.3}');
    localStorage.setItem('unrelated', 'keep');

    purgeLegacyStorageKeys();

    expect(localStorage.getItem('mediahub.theme')).toBeNull();
    expect(localStorage.getItem('mediahub_selected_team')).toBeNull();
    expect(localStorage.getItem('mediahub.project.p1.ep')).toBeNull();
    expect(sessionStorage.getItem('mediahub_error_session_id')).toBeNull();
    expect(localStorage.getItem('mediahub_volume_pref')).toBe('{"volume":0.3}');
    expect(localStorage.getItem(STORAGE_KEYS.theme)).toBe('light');
    expect(localStorage.getItem(STORAGE_KEYS.selectedTeam)).toBe('team-1');
    expect(localStorage.getItem(projectEpisodeKey('p1'))).toBe('2');
    expect(localStorage.getItem('unrelated')).toBe('keep');
  });

  it('never throws when storage throws', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const broken = brokenStorage();
    expect(() => purgeLegacyStorageKeys({ local: broken, session: broken })).not.toThrow();
  });
});

describe('functional keys survive the rename end to end', () => {
  it('team selection and api key still reach apiClient headers after migration', async () => {
    localStorage.setItem('mediahub_selected_team', 'team-42');
    localStorage.setItem('mediahub_api_key', 'sk_live_old');
    migrateLegacyStorageKeys();
    const { buildAuthHeaders } = await import('../services/apiClient');
    const headers = await buildAuthHeaders();
    expect(headers['X-Team-Id']).toBe('team-42');
    expect(headers['X-API-Key']).toBe('sk_live_old');
  });

  it('persisted global chat window state survives (zustand persist hydrates from the new key)', async () => {
    localStorage.setItem(
      'mediahub.global_chat',
      JSON.stringify({ state: { open: true, width: 777 }, version: 0 }),
    );
    migrateLegacyStorageKeys();
    vi.resetModules();
    const { useGlobalChatStore } = await import('../stores/globalChatStore');
    expect(useGlobalChatStore.getState().open).toBe(true);
    expect(useGlobalChatStore.getState().width).toBe(777);
  });
});

describe('storageKeysBoot (migrate, then purge)', () => {
  const runBoot = async (): Promise<void> => {
    vi.resetModules();
    await import('./storageKeysBoot');
  };

  it('calls the purge, and only after the migration', () => {
    const src = readFileSync(path.resolve(__dirname, 'storageKeysBoot.ts'), 'utf8');
    const calls = src
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => /^(migrate|purge)LegacyStorageKeys\(\);$/.test(l));
    expect(calls).toEqual(['migrateLegacyStorageKeys();', 'purgeLegacyStorageKeys();']);
  });

  it('a boot moves legacy values to the new keys and removes the old ones', async () => {
    localStorage.setItem('mediahub_selected_team', 'team-5');
    localStorage.setItem('mediahub.project.p1.ep', '2');
    sessionStorage.setItem('mediahub_auth_expired', '1');

    await runBoot();

    expect(localStorage.getItem(STORAGE_KEYS.selectedTeam)).toBe('team-5');
    expect(localStorage.getItem(projectEpisodeKey('p1'))).toBe('2');
    expect(sessionStorage.getItem(SESSION_KEYS.authExpired)).toBe('1');
    expect(localStorage.getItem('mediahub_selected_team')).toBeNull();
    expect(localStorage.getItem('mediahub.project.p1.ep')).toBeNull();
    expect(sessionStorage.getItem('mediahub_auth_expired')).toBeNull();
  });

  it('a boot whose migration fails (marker not written) purges nothing, and the next boot finishes', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    localStorage.setItem('mediahub.theme', 'light');
    localStorage.setItem('mediahub_selected_team', 'team-9');
    const real = localStorage.setItem.bind(localStorage);
    const spy = vi.spyOn(localStorage, 'setItem').mockImplementation((k: string, v: string) => {
      if (k === STORAGE_KEYS.theme) throw new Error('QuotaExceededError');
      return real(k, v);
    });

    await runBoot();

    expect(localStorage.getItem(MIGRATION_MARKER_KEY)).toBeNull();
    // selected_team WAS copied, yet its old key must stay: no marker, no purge.
    expect(localStorage.getItem(STORAGE_KEYS.selectedTeam)).toBe('team-9');
    expect(localStorage.getItem('mediahub_selected_team')).toBe('team-9');
    expect(localStorage.getItem('mediahub.theme')).toBe('light');

    spy.mockRestore();
    await runBoot();

    expect(localStorage.getItem(STORAGE_KEYS.theme)).toBe('light');
    expect(localStorage.getItem('mediahub.theme')).toBeNull();
    expect(localStorage.getItem('mediahub_selected_team')).toBeNull();
  });
});

describe('boot ordering', () => {
  it('index.tsx runs the migration as its very first import, before any reader module evaluates', () => {
    const src = readFileSync(path.resolve(__dirname, '../index.tsx'), 'utf8');
    const firstImport = src.split('\n').find((l) => /^import\s/.test(l));
    expect(firstImport).toBe("import './utils/storageKeysBoot';");
  });

  it('the boot module and the key module import nothing (so nothing can evaluate before them)', () => {
    for (const f of ['storageKeys.ts', 'storageKeysBoot.ts']) {
      const src = readFileSync(path.resolve(__dirname, f), 'utf8');
      const imports = src.split('\n').filter((l) => /^import\s/.test(l));
      const foreign = imports.filter((l) => !l.includes("'./storageKeys'"));
      expect(foreign).toEqual([]);
    }
  });

  it('index.html pre-paint script reads the new theme key, falling back to the legacy one', () => {
    const html = readFileSync(path.resolve(__dirname, '../index.html'), 'utf8');
    const iNew = html.indexOf(`localStorage.getItem('${STORAGE_KEYS.theme}')`);
    const iOld = html.indexOf("localStorage.getItem('mediahub.theme')");
    expect(iNew).toBeGreaterThan(-1);
    expect(iOld).toBeGreaterThan(iNew);
  });
});
