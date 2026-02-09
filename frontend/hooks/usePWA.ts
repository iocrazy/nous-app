import { useState, useEffect, useCallback } from 'react';
import { registerSW } from 'virtual:pwa-register';

export function usePWA() {
  const [needRefresh, setNeedRefresh] = useState(false);
  const [offlineReady, setOfflineReady] = useState(false);
  const [updateSW, setUpdateSW] = useState<((reloadPage?: boolean) => Promise<void>) | null>(null);

  useEffect(() => {
    const update = registerSW({
      onNeedRefresh() {
        setNeedRefresh(true);
      },
      onOfflineReady() {
        setOfflineReady(true);
      },
    });
    setUpdateSW(() => update);
  }, []);

  const applyUpdate = useCallback(() => {
    updateSW?.(true);
  }, [updateSW]);

  const dismissUpdate = useCallback(() => {
    setNeedRefresh(false);
    setOfflineReady(false);
  }, []);

  return { needRefresh, offlineReady, applyUpdate, dismissUpdate };
}
