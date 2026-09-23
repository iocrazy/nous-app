import { readFileSync } from 'fs';
import path from 'path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  LEGACY_LOCAL_KEYS,
  LEGACY_SESSION_KEYS,
  STORAGE_KEYS,
  SESSION_KEYS,
  migrateLegacyStorageKeys,
  projectEpisodeKey,
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

describe('migrateLegacyStorageKeys', () => {
  it('moves every exact legacy localStorage key to its new name', () => {
    for (const oldKey of Object.keys(LEGACY_LOCAL_KEYS)) {
      localStorage.setItem(oldKey, `v:${oldKey}`);
    }
    migrateLegacyStorageKeys();
    for (const [oldKey, newKey] of Object.entries(LEGACY_LOCAL_KEYS)) {
      expect(localStorage.getItem(newKey)).toBe(`v:${oldKey}`);
      expect(localStorage.getItem(oldKey)).toBeNull();
    }
  });

  it('moves the sessionStorage keys too', () => {
    for (const oldKey of Object.keys(LEGACY_SESSION_KEYS)) {
      sessionStorage.setItem(oldKey, '1');
    }
    migrateLegacyStorageKeys();
    for (const [oldKey, newKey] of Object.entries(LEGACY_SESSION_KEYS)) {
      expect(sessionStorage.getItem(newKey)).toBe('1');
      expect(sessionStorage.getItem(oldKey)).toBeNull();
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
    expect(localStorage.getItem('mediahub_selected_team')).toBeNull();
  });

  it('is idempotent', () => {
    localStorage.setItem('mediahub.theme', 'light');
    migrateLegacyStorageKeys();
    migrateLegacyStorageKeys();
    expect(localStorage.getItem(STORAGE_KEYS.theme)).toBe('light');
    expect(localStorage.getItem('mediahub.theme')).toBeNull();
  });

  it('migrates the prefix families (per-project episode, todolist columns / attention)', () => {
    localStorage.setItem('mediahub.project.p1.ep', '2');
    localStorage.setItem('mediahub:todolist:columns:team:7', '["id"]');
    localStorage.setItem('mediahub:todolist:attention:me', '1');
    localStorage.setItem('unrelated', 'keep');
    migrateLegacyStorageKeys();
    expect(localStorage.getItem(projectEpisodeKey('p1'))).toBe('2');
    expect(localStorage.getItem(`${TODOLIST_COLUMNS_PREFIX}:team:7`)).toBe('["id"]');
    expect(localStorage.getItem(`${TODOLIST_ATTENTION_PREFIX}:me`)).toBe('1');
    expect(localStorage.getItem('mediahub.project.p1.ep')).toBeNull();
    expect(localStorage.getItem('mediahub:todolist:columns:team:7')).toBeNull();
    expect(localStorage.getItem('mediahub:todolist:attention:me')).toBeNull();
    expect(localStorage.getItem('unrelated')).toBe('keep');
    expect(localStorage.length).toBe(4);
  });

  it('does not overwrite an existing prefixed value either', () => {
    localStorage.setItem('mediahub.project.p1.ep', '1');
    localStorage.setItem(projectEpisodeKey('p1'), '3');
    migrateLegacyStorageKeys();
    expect(localStorage.getItem(projectEpisodeKey('p1'))).toBe('3');
    expect(localStorage.getItem('mediahub.project.p1.ep')).toBeNull();
  });

  it('never throws when storage throws, and logs instead', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    const broken = {
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
    } as unknown as Storage;
    expect(() => migrateLegacyStorageKeys({ local: broken, session: broken })).not.toThrow();
    expect(err).toHaveBeenCalled();
  });

  it('keeps migrating the remaining keys when one write fails (quota)', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    localStorage.setItem('mediahub.theme', 'light');
    localStorage.setItem('mediahub_selected_team', 'team-9');
    const real = localStorage.setItem.bind(localStorage);
    vi.spyOn(localStorage, 'setItem').mockImplementation((k: string, v: string) => {
      if (k === STORAGE_KEYS.theme) throw new Error('QuotaExceededError');
      return real(k, v);
    });
    migrateLegacyStorageKeys();
    expect(localStorage.getItem(STORAGE_KEYS.selectedTeam)).toBe('team-9');
    // The failed copy must NOT delete the only surviving copy of the value.
    expect(localStorage.getItem('mediahub.theme')).toBe('light');
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
