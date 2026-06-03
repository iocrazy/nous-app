import React from 'react';
import { useVersionCheck } from '../hooks/useVersionCheck';

/**
 * Shows the running build version, plus an "update to vX.Y.Z" link when the
 * live deployment is newer (so the user can see they're behind and refresh
 * without hunting for the corner toast). A plain reload pulls the fresh
 * bundle — the autoUpdate service worker already took over.
 */
export function VersionBadge() {
  const { currentVersion, latestVersion, updateAvailable } = useVersionCheck();
  return (
    <>
      v{currentVersion}
      {updateAvailable && latestVersion && (
        <>
          {' · '}
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="text-indigo-400 hover:text-indigo-300 underline underline-offset-2"
          >
            update to v{latestVersion}
          </button>
        </>
      )}
    </>
  );
}
