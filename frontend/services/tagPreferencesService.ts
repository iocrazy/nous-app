import { getAuthHeaders } from './parserService';

const API_BASE = import.meta.env.VITE_API_URL || '';

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

export async function fetchTagPreferences(): Promise<TagPreferences> {
  try {
    const res = await fetch(`${API_BASE}/api/v1/tags/preferences`, {
      headers: await getAuthHeaders(),
    });
    if (!res.ok) return { ...DEFAULTS };
    return await res.json();
  } catch (err) {
    console.error('Failed to fetch tag preferences:', err);
    return { ...DEFAULTS };
  }
}

export async function updateTagPreferences(
  updates: Partial<TagPreferences>,
): Promise<TagPreferences> {
  const res = await fetch(`${API_BASE}/api/v1/tags/preferences`, {
    method: 'PATCH',
    headers: {
      ...(await getAuthHeaders()),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`Failed to update preferences: ${res.status}`);
  return await res.json();
}
