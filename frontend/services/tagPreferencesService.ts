import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

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
  // Return cached result if available
  if (_cachedPrefs) return { ..._cachedPrefs };
  // Deduplicate concurrent requests
  if (_fetchPromise) return _fetchPromise;

  _fetchPromise = (async () => {
    try {
      const res = await fetch(`${getApiUrl()}/api/v1/tags/preferences`, {
        headers: await getAuthHeaders(),
      });
      if (!res.ok) return { ...DEFAULTS };
      const data = await res.json();
      _cachedPrefs = data;
      return data;
    } catch (err) {
      console.error('Failed to fetch tag preferences:', err);
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
  const res = await fetch(`${getApiUrl()}/api/v1/tags/preferences`, {
    method: 'PATCH',
    headers: {
      ...(await getAuthHeaders()),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`Failed to update preferences: ${res.status}`);
  const data = await res.json();
  _cachedPrefs = data; // Update cache with latest
  return data;
}
