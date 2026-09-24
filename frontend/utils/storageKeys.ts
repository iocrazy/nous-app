// frontend/utils/storageKeys.ts
//
// Single source of truth for every browser-storage key the app owns, plus the
// one-shot migration from the pre-rename `mediahub*` names.
//
// ⚠️ This module must import NOTHING. `storageKeysBoot.ts` runs the migration
// as the first import of `index.tsx`; any import here would evaluate before
// the migration and could read a key that has not been moved yet (zustand
// persist, for example, hydrates at store-creation time).

/** localStorage keys. */
export const STORAGE_KEYS = {
  theme: 'nous.theme',
  globalChat: 'nous.global_chat',
  projectsView: 'nous.projects.view',
  resourcesViewMode: 'nous_resources_view_mode',
  resourcesFlatten: 'nous_resources_flatten',
  playbackPositions: 'nous_playback_positions_v1',
  searchScope: 'nous_search_scope_v2',
  /** Pre-v2 search scope; still read once by `loadSearchScope` to upgrade. */
  searchScopeLegacy: 'nous_search_scope',
  resourceSearchScope: 'nous_resource_search_scope',
  clockDriftDismissed: 'nous_clock_drift_dismissed',
  qualityPref: 'nous_quality_pref',
  volumePref: 'nous_volume_pref',
  libraryPreferences: 'nous_library_preferences',
  /** Drives the `X-Team-Id` request header. */
  selectedTeam: 'nous_selected_team',
  personalTeam: 'nous_personal_team',
  /** Auth credential (`X-API-Key`). */
  apiKey: 'nous_api_key',
} as const;

/** sessionStorage keys. */
export const SESSION_KEYS = {
  errorSessionId: 'nous_error_session_id',
  authExpired: 'nous_auth_expired',
} as const;

const PROJECT_KEY_PREFIX = 'nous.project.';
export const TODOLIST_COLUMNS_PREFIX = 'nous:todolist:columns';
export const TODOLIST_ATTENTION_PREFIX = 'nous:todolist:attention';

/** Per-project "last opened episode" key. */
export const projectEpisodeKey = (projectId: string): string =>
  `${PROJECT_KEY_PREFIX}${projectId}.ep`;

/** Exact old → new localStorage names. */
export const LEGACY_LOCAL_KEYS: Readonly<Record<string, string>> = {
  'mediahub.theme': STORAGE_KEYS.theme,
  'mediahub.global_chat': STORAGE_KEYS.globalChat,
  'mediahub.projects.view': STORAGE_KEYS.projectsView,
  mediahub_resources_view_mode: STORAGE_KEYS.resourcesViewMode,
  mediahub_resources_flatten: STORAGE_KEYS.resourcesFlatten,
  mediahub_playback_positions_v1: STORAGE_KEYS.playbackPositions,
  mediahub_search_scope_v2: STORAGE_KEYS.searchScope,
  mediahub_search_scope: STORAGE_KEYS.searchScopeLegacy,
  mediahub_resource_search_scope: STORAGE_KEYS.resourceSearchScope,
  mediahub_clock_drift_dismissed: STORAGE_KEYS.clockDriftDismissed,
  mediahub_quality_pref: STORAGE_KEYS.qualityPref,
  mediahub_volume_pref: STORAGE_KEYS.volumePref,
  mediahub_library_preferences: STORAGE_KEYS.libraryPreferences,
  mediahub_selected_team: STORAGE_KEYS.selectedTeam,
  mediahub_personal_team: STORAGE_KEYS.personalTeam,
  mediahub_api_key: STORAGE_KEYS.apiKey,
};

/** Exact old → new sessionStorage names. */
export const LEGACY_SESSION_KEYS: Readonly<Record<string, string>> = {
  mediahub_error_session_id: SESSION_KEYS.errorSessionId,
  mediahub_auth_expired: SESSION_KEYS.authExpired,
};

/** Old → new prefixes for key families (the suffix is carried over verbatim). */
export const LEGACY_LOCAL_PREFIXES: ReadonlyArray<readonly [string, string]> = [
  ['mediahub.project.', PROJECT_KEY_PREFIX],
  ['mediahub:todolist:columns', TODOLIST_COLUMNS_PREFIX],
  ['mediahub:todolist:attention', TODOLIST_ATTENTION_PREFIX],
];

/** Set in each storage once every legacy value has been copied. The copy runs
 *  only while it is absent, so a legacy key that outlives the purge (its copy
 *  failed, or the storage cannot be enumerated) is never copied again to
 *  resurrect a value the app removed on purpose — an invalid selected team
 *  dropped by `useTeams`, or the one-shot `auth_expired` flag LoginPage consumes.
 *  It is also the purge's go-ahead: no marker, no purge (see below). */
export const MIGRATION_MARKER_KEY = 'nous_storage_keys_migrated_v1';

type StorageTargets = { local?: Storage | null; session?: Storage | null };

/** Copy one key. An existing new value always wins; the old key is left in
 *  place. Returns false only when the copy was needed and failed (quota). */
function copyKey(storage: Storage, oldKey: string, newKey: string): boolean {
  try {
    const oldValue = storage.getItem(oldKey);
    if (oldValue !== null && storage.getItem(newKey) === null) {
      storage.setItem(newKey, oldValue);
    }
    return true;
  } catch (err) {
    console.error(`[storageKeys] failed to copy "${oldKey}" → "${newKey}"`, err);
    return false;
  }
}

/** Remove the old key, but only if its value is safely held under the new key. */
function removeLegacyKey(storage: Storage, oldKey: string, newKey: string): void {
  try {
    if (storage.getItem(oldKey) !== null && storage.getItem(newKey) !== null) {
      storage.removeItem(oldKey);
    }
  } catch (err) {
    console.error(`[storageKeys] failed to purge "${oldKey}"`, err);
  }
}

function listKeys(storage: Storage): string[] {
  const keys: string[] = [];
  for (let i = 0; i < storage.length; i += 1) {
    const k = storage.key(i);
    if (k !== null) keys.push(k);
  }
  return keys;
}

/** Every (old, new) pair present in this storage: the exact map plus whatever
 *  prefixed keys exist. Null when the storage cannot be enumerated. */
function legacyPairs(
  storage: Storage,
  exact: Readonly<Record<string, string>>,
  prefixes: ReadonlyArray<readonly [string, string]>,
): Array<readonly [string, string]> | null {
  const pairs: Array<readonly [string, string]> = Object.entries(exact);
  if (prefixes.length === 0) return pairs;
  let keys: string[];
  try {
    keys = listKeys(storage);
  } catch (err) {
    console.error('[storageKeys] failed to enumerate storage for prefix keys', err);
    return null;
  }
  for (const key of keys) {
    const hit = prefixes.find(([oldPrefix]) => key.startsWith(oldPrefix));
    if (hit) pairs.push([key, hit[1] + key.slice(hit[0].length)]);
  }
  return pairs;
}

function migrateStorage(
  storage: Storage,
  exact: Readonly<Record<string, string>>,
  prefixes: ReadonlyArray<readonly [string, string]>,
): void {
  try {
    if (storage.getItem(MIGRATION_MARKER_KEY) !== null) return;
  } catch (err) {
    console.error('[storageKeys] failed to read migration marker', err);
    return;
  }
  const pairs = legacyPairs(storage, exact, prefixes);
  if (pairs === null) return;
  // Copy everything (no short-circuit), then mark done only if nothing failed,
  // so a quota failure is retried on the next boot instead of being lost.
  const results = pairs.map(([oldKey, newKey]) => copyKey(storage, oldKey, newKey));
  if (results.every(Boolean)) {
    try {
      storage.setItem(MIGRATION_MARKER_KEY, '1');
    } catch (err) {
      console.error('[storageKeys] failed to write migration marker', err);
    }
  }
}

function resolveStorage(pick: () => Storage): Storage | null {
  try {
    return typeof window === 'undefined' ? null : pick();
  } catch (err) {
    // Accessing window.localStorage itself throws when storage is blocked.
    console.error('[storageKeys] storage unavailable', err);
    return null;
  }
}

function resolveTargets(storages: StorageTargets): {
  local: Storage | null;
  session: Storage | null;
} {
  return {
    local: storages.local ?? resolveStorage(() => window.localStorage),
    session: storages.session ?? resolveStorage(() => window.sessionStorage),
  };
}

/** Copy every `mediahub*` key to its `nous*` name. Runs once per storage
 *  (guarded by MIGRATION_MARKER_KEY), never overwrites a new value, never
 *  throws. Must run before any reader.
 *
 *  Copy only — deleting the old keys is `purgeLegacyBrowserStorageEntries`' job, which
 *  `storageKeysBoot.ts` runs right after this. */
export function migrateLegacyStorageKeys(storages: StorageTargets = {}): void {
  try {
    const { local, session } = resolveTargets(storages);
    if (local) migrateStorage(local, LEGACY_LOCAL_KEYS, LEGACY_LOCAL_PREFIXES);
    if (session) migrateStorage(session, LEGACY_SESSION_KEYS, []);
  } catch (err) {
    console.error('[storageKeys] legacy key migration failed', err);
  }
}

/** Delete the legacy `mediahub*` keys. Two guards, both per storage, so
 *  nothing whose copy failed is ever lost:
 *  - the storage must carry MIGRATION_MARKER_KEY — a migration that did not
 *    finish (quota, unreadable storage) leaves every legacy key in place for
 *    the next boot's retry;
 *  - each old key is removed only when its `nous*` counterpart holds a value.
 *
 *  Called from `storageKeysBoot.ts` right after `migrateLegacyStorageKeys()`.
 *  Once it runs, rolling the frontend back past the rename release loses the
 *  users' stored team selection / API key / preferences (the old build reads
 *  only the `mediahub*` names). */
export function purgeLegacyBrowserStorageEntries(storages: StorageTargets = {}): void {
  try {
    const { local, session } = resolveTargets(storages);
    const run = (
      storage: Storage,
      exact: Readonly<Record<string, string>>,
      prefixes: ReadonlyArray<readonly [string, string]>,
    ): void => {
      try {
        if (storage.getItem(MIGRATION_MARKER_KEY) === null) return;
      } catch (err) {
        console.error('[storageKeys] failed to read migration marker before purge', err);
        return;
      }
      const pairs = legacyPairs(storage, exact, prefixes);
      if (pairs === null) return;
      for (const [oldKey, newKey] of pairs) removeLegacyKey(storage, oldKey, newKey);
    };
    if (local) run(local, LEGACY_LOCAL_KEYS, LEGACY_LOCAL_PREFIXES);
    if (session) run(session, LEGACY_SESSION_KEYS, []);
  } catch (err) {
    console.error('[storageKeys] legacy key purge failed', err);
  }
}
