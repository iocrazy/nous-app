import { useState, useEffect, useCallback } from 'react';
import { shouldOfferUpdate } from '../utils/versionCompare';

declare const __APP_VERSION__: string;

interface VersionInfo {
  version: string;
  commitSha?: string;
  buildTime?: string;
}

/**
 * Compare this tab's baked-in build version against the live deployment's
 * /version.json. Checks on mount and whenever the tab becomes visible again
 * (the moment a long-open tab would care that a newer build shipped) — NO
 * polling timer. Fetch failures are swallowed (logged) so a transient blip
 * never nags the user with a phantom update.
 */
export function useVersionCheck() {
  const currentVersion = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : undefined;
  const [latestVersion, setLatestVersion] = useState<string | null>(null);

  const check = useCallback(async () => {
    try {
      const resp = await fetch(`/version.json?t=${Date.now()}`, { cache: 'no-store' });
      if (!resp.ok) return;
      const data = (await resp.json()) as VersionInfo;
      if (data?.version) setLatestVersion(data.version);
    } catch (err) {
      console.error('[useVersionCheck] version.json fetch failed:', err);
    }
  }, []);

  useEffect(() => {
    check();
    const onVisible = () => {
      if (document.visibilityState === 'visible') check();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [check]);

  return {
    currentVersion,
    latestVersion,
    updateAvailable: shouldOfferUpdate(currentVersion, latestVersion),
  };
}
