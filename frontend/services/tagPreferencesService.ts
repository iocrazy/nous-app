import { apiClient, ApiError } from './apiClient';

export interface PickerSettings {
  layout: 'list' | 'grid';
  columnWidth: 'small' | 'medium' | 'large';
  showStarred: boolean;
  showRecently: boolean;
  showRecommended: boolean;
  showCount: boolean;
}

export interface PanelSize {
  width: number;
  height: number;
}

export interface TagPreferences {
  starred_tag_ids: string[];
  picker_settings: PickerSettings;
  panel_size: PanelSize;
}

const DEFAULTS: TagPreferences = {
  starred_tag_ids: [],
  picker_settings: {
    layout: 'list',
    columnWidth: 'medium',
    showStarred: true,
    showRecently: true,
    showRecommended: false,
    showCount: true,
  },
  panel_size: { width: 480, height: 400 },
};

// Module-level cache to avoid duplicate requests across EagleTagPicker instances
let _cachedPrefs: TagPreferences | null = null;
let _fetchPromise: Promise<TagPreferences> | null = null;

export async function fetchTagPreferences(): Promise<TagPreferences> {
  if (_cachedPrefs) return { ..._cachedPrefs };
  if (_fetchPromise) return _fetchPromise;

  _fetchPromise = (async () => {
    try {
      const data = await apiClient.get<TagPreferences>(
        '/api/v1/tags/preferences',
      );
      _cachedPrefs = data;
      return data;
    } catch (err) {
      // Any failure (404, 5xx, network) falls back to defaults so the
      // picker UI never breaks just because preferences are missing.
      if (!(err instanceof ApiError)) {
        console.error('Failed to fetch tag preferences:', err);
      }
      return { ...DEFAULTS };
    } finally {
      _fetchPromise = null;
    }
  })();
  return _fetchPromise;
}

/** Invalidate preferences cache (called after update) */
export function invalidatePreferencesCache() {
  _cachedPrefs = null;
}

export async function updateTagPreferences(
  updates: Partial<TagPreferences>,
): Promise<TagPreferences> {
  const data = await apiClient.patch<TagPreferences>(
    '/api/v1/tags/preferences',
    updates,
  );
  _cachedPrefs = data;
  return data;
}
