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

/** Move one key. A new value that already exists always wins. The old key is
 *  removed only after the new one is known to hold a value, so a failed write
 *  (quota) never destroys the only surviving copy. */
function moveKey(storage: Storage, oldKey: string, newKey: string): void {
  try {
    const oldValue = storage.getItem(oldKey);
    if (oldValue === null) return;
    if (storage.getItem(newKey) === null) storage.setItem(newKey, oldValue);
    storage.removeItem(oldKey);
  } catch (err) {
    console.error(`[storageKeys] failed to migrate "${oldKey}" → "${newKey}"`, err);
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

function migrateStorage(
  storage: Storage,
  exact: Readonly<Record<string, string>>,
  prefixes: ReadonlyArray<readonly [string, string]>,
): void {
  for (const [oldKey, newKey] of Object.entries(exact)) moveKey(storage, oldKey, newKey);
  if (prefixes.length === 0) return;
  let keys: string[];
  try {
    keys = listKeys(storage);
  } catch (err) {
    console.error('[storageKeys] failed to enumerate storage for prefix migration', err);
    return;
  }
  for (const key of keys) {
    for (const [oldPrefix, newPrefix] of prefixes) {
      if (key.startsWith(oldPrefix)) {
        moveKey(storage, key, newPrefix + key.slice(oldPrefix.length));
        break;
      }
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

/** Move every `mediahub*` key to its `nous*` name. Synchronous, idempotent,
 *  never overwrites a new value, never throws. Must run before any reader. */
export function migrateLegacyStorageKeys(
  storages: { local?: Storage | null; session?: Storage | null } = {},
): void {
  try {
    const local = storages.local ?? resolveStorage(() => window.localStorage);
    const session = storages.session ?? resolveStorage(() => window.sessionStorage);
    if (local) migrateStorage(local, LEGACY_LOCAL_KEYS, LEGACY_LOCAL_PREFIXES);
    if (session) migrateStorage(session, LEGACY_SESSION_KEYS, []);
  } catch (err) {
    console.error('[storageKeys] legacy key migration failed', err);
  }
}
