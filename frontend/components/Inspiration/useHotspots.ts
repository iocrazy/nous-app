// frontend/components/Inspiration/useHotspots.ts
// Shared hotspot loading + optimistic state engine, extracted from the legacy
// TopicInspirationPage so both the Hotspots tab and the Notes-tab Top-3 panel
// drive off one source. Day comes from the page-wide selected date.
import { useCallback, useEffect, useState } from 'react';
import {
  getHotspots,
  setHotspotState,
  type Hotspot,
  type HotspotStatePatch,
} from '../../services/topicService';

export function useHotspots(opts: { day?: string; enabled: boolean }) {
  const { day, enabled } = opts;
  const [hotspots, setHotspots] = useState<Hotspot[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const rows = await getHotspots(day, undefined, undefined, 'all', undefined);
        if (alive) setHotspots(rows);
      } catch (err) {
        if (alive) {
          setError((err as Error).message);
          setHotspots([]);
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [day, enabled, reloadKey]);

  const applyState = useCallback(
    async (h: Hotspot, patch: HotspotStatePatch) => {
      setHotspots((prev) => prev.map((x) => (x.id === h.id ? { ...x, ...patch } : x)));
      try {
        await setHotspotState(h.id, patch);
      } catch (err) {
        reload(); // revert via refetch
        throw err;
      }
    },
    [reload],
  );

  return { hotspots, loading, error, reload, applyState };
}
