import React, { useEffect, useState } from 'react';
import { RefreshCw, Wifi, X } from 'lucide-react';
import { usePWA } from '../hooks/usePWA';
import { useVersionCheck } from '../hooks/useVersionCheck';

export default function PWAUpdatePrompt() {
  const { needRefresh, offlineReady, dismissUpdate } = usePWA();
  // version.json self-check is the reliable signal under registerType:'autoUpdate'
  // (where onNeedRefresh never fires). It also gives us the real version numbers.
  const { currentVersion, latestVersion, updateAvailable } = useVersionCheck();

  // Remember which live version the user dismissed so the toast stays hidden
  // until an even newer build ships (latestVersion changes again).
  const [dismissedVersion, setDismissedVersion] = useState<string | null>(null);
  const versionDismissed = !!latestVersion && dismissedVersion === latestVersion;
  const showUpdate = (needRefresh || updateAvailable) && !versionDismissed;

  useEffect(() => {
    if (offlineReady && !showUpdate) {
      const timer = setTimeout(dismissUpdate, 5000);
      return () => clearTimeout(timer);
    }
  }, [offlineReady, showUpdate, dismissUpdate]);

  const [updating, setUpdating] = useState(false);

  const onUpdate = async () => {
    // A stuck service worker keeps serving its OLD precached app shell, so a
    // plain reload is answered from that cache and NEVER escapes — observed in
    // the wild: a tab pinned to an old version across multiple deploys, with
    // both the button and manual refresh failing to upgrade. The only reliable
    // escape is to unregister the SW + delete all caches, THEN reload: with no
    // controller and no cache, the browser fetches the fresh build from origin
    // and re-registers a clean SW. These ops are fast (unlike reg.update(),
    // which can stall on iOS Safari), and the reload always runs in finally.
    setUpdating(true);
    try {
      if ('serviceWorker' in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        await Promise.all(regs.map((r) => r.unregister().catch(() => false)));
      }
      if (typeof caches !== 'undefined') {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k).catch(() => false)));
      }
    } catch (err) {
      console.error('[PWAUpdatePrompt] SW/cache reset failed:', err);
    } finally {
      window.location.reload();
    }
  };

  const onDismiss = () => {
    dismissUpdate();
    if (latestVersion) setDismissedVersion(latestVersion);
  };

  if (!showUpdate && !offlineReady) return null;

  return (
    <div className="fixed bottom-20 sm:bottom-4 left-4 right-4 sm:left-auto sm:right-4 z-[9999] max-w-sm animate-in slide-in-from-bottom-4 fade-in duration-300">
      <div className="bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl p-4">
        {showUpdate ? (
          <>
            <div className="flex items-start gap-3">
              <RefreshCw className="w-5 h-5 text-indigo-400 mt-0.5 shrink-0" />
              <div className="flex-1">
                <p className="text-sm font-medium text-zinc-100">Update Available</p>
                <p className="text-xs text-zinc-400 mt-1">
                  {currentVersion && latestVersion
                    ? `v${currentVersion} → v${latestVersion}`
                    : 'A new version of MediaHub is ready.'}
                </p>
              </div>
              <button
                onClick={onDismiss}
                className="text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex gap-2 mt-3 ml-8">
              <button
                onClick={onUpdate}
                disabled={updating}
                className="px-3 py-1.5 text-xs font-medium bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 text-white rounded-lg transition-colors"
              >
                {updating ? 'Updating…' : 'Update Now'}
              </button>
              <button
                onClick={onDismiss}
                className="px-3 py-1.5 text-xs font-medium text-zinc-400 hover:text-zinc-200 transition-colors"
              >
                Later
              </button>
            </div>
          </>
        ) : (
          <div className="flex items-center gap-3">
            <Wifi className="w-5 h-5 text-emerald-400 shrink-0" />
            <p className="text-sm text-zinc-200">App ready to work offline</p>
            <button
              onClick={dismissUpdate}
              className="ml-auto text-zinc-500 hover:text-zinc-300 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
