import { useState, useEffect, useCallback, useRef } from 'react';
import {
  fetchTagPreferences,
  updateTagPreferences,
  type TagPreferences,
  type PickerSettings,
  type PanelSize,
} from '../../services/tagPreferencesService';

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

export function useTagPreferences() {
  const [prefs, setPrefs] = useState<TagPreferences>(DEFAULTS);
  const [loading, setLoading] = useState(true);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    fetchTagPreferences()
      .then(setPrefs)
      .finally(() => setLoading(false));
  }, []);

  const update = useCallback(
    (updates: Partial<TagPreferences>) => {
      // Optimistic update
      setPrefs((prev) => ({
        ...prev,
        ...updates,
        picker_settings: updates.picker_settings
          ? { ...prev.picker_settings, ...updates.picker_settings }
          : prev.picker_settings,
      }));

      // Debounced persist
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        updateTagPreferences(updates).catch((err) =>
          console.error('Failed to save preferences:', err),
        );
      }, 500);
    },
    [],
  );

  const toggleStar = useCallback(
    (tagId: string) => {
      const current = prefs.starred_tag_ids;
      const next = current.includes(tagId)
        ? current.filter((id) => id !== tagId)
        : [...current, tagId];
      update({ starred_tag_ids: next });
    },
    [prefs.starred_tag_ids, update],
  );

  const updateSettings = useCallback(
    (partial: Partial<PickerSettings>) => {
      update({ picker_settings: partial as any });
    },
    [update],
  );

  const updatePanelSize = useCallback(
    (size: PanelSize) => {
      update({ panel_size: size });
    },
    [update],
  );

  return {
    prefs,
    loading,
    toggleStar,
    updateSettings,
    updatePanelSize,
  };
}
