// frontend/hooks/useFilterBarVisibility.ts
//
// Persistent show/hide state for the Resources filter bar. Defaults to
// "visible" so the bar keeps its current behaviour for users who've
// never toggled it; once toggled the preference survives reloads.
//
// Isolated from useFilterBarConfig so that visibility doesn't reset when
// a user clears all filters (a common muscle-memory action) and so that
// the config panel and the visibility toggle can evolve independently.

import { useCallback, useEffect, useState } from 'react';

const STORAGE_KEY = 'resourceFilterBarVisible';

function readInitial(): boolean {
  if (typeof window === 'undefined') return true;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === null) return true;
    // Accept both "true"/"false" strings and legacy JSON booleans.
    if (raw === 'true') return true;
    if (raw === 'false') return false;
    const parsed = JSON.parse(raw);
    return parsed !== false;
  } catch (err) {
    console.error('[useFilterBarVisibility] Failed to read stored value:', err);
    return true;
  }
}

export interface UseFilterBarVisibilityReturn {
  visible: boolean;
  setVisible: (next: boolean) => void;
  toggle: () => void;
}

export function useFilterBarVisibility(): UseFilterBarVisibilityReturn {
  const [visible, setVisibleState] = useState<boolean>(() => readInitial());

  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(STORAGE_KEY, visible ? 'true' : 'false');
    } catch (err) {
      console.error('[useFilterBarVisibility] Failed to persist value:', err);
    }
  }, [visible]);

  const setVisible = useCallback((next: boolean) => setVisibleState(next), []);
  const toggle = useCallback(() => setVisibleState((prev) => !prev), []);

  return { visible, setVisible, toggle };
}
