import React, { useState } from 'react';
import { useVersionCheck } from '../hooks/useVersionCheck';
import { resetServiceWorkerAndReload } from '../utils/swReset';

/**
 * Shows the running build version, plus an "update to vX.Y.Z" link when the
 * live deployment is newer.
 *
 * Clicking it does a FULL service-worker reset (unregister + clear caches +
 * reload), NOT a plain reload. A plain reload is answered by the stale SW from
 * its old precache, so it just flashed and came back on the same version — the
 * exact symptom reported. The reset is the only reliable escape (same as the
 * Settings "Clear Cache & Reload" action). Shows "updating…" so it doesn't feel
 * like nothing happened.
 */
export function VersionBadge() {
  const { currentVersion, latestVersion, updateAvailable } = useVersionCheck();
  const [updating, setUpdating] = useState(false);

  return (
    <>
      v{currentVersion}
      {updateAvailable && latestVersion && (
        <>
          {' · '}
          <button
            type="button"
            disabled={updating}
            onClick={() => {
              setUpdating(true);
              resetServiceWorkerAndReload();
            }}
            className="text-indigo-400 hover:text-indigo-300 underline underline-offset-2 disabled:opacity-60 disabled:no-underline"
          >
            {updating ? 'updating…' : `update to v${latestVersion}`}
          </button>
        </>
      )}
    </>
  );
}
