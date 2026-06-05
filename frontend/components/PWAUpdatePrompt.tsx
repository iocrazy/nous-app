import { usePWA } from '../hooks/usePWA';

/**
 * Service-worker mount point. NO UI.
 *
 * Updates apply silently in the BACKGROUND: usePWA registers the autoUpdate SW
 * (skipWaiting + clientsClaim) and forces periodic re-checks, so a new build
 * installs in the background and takes over on the next navigation/relaunch —
 * no nagging "Update Available" banner (removed by request). The manual
 * "Clear cache & reload" escape for a stuck SW lives in Settings
 * (MobileProfilePage → General).
 */
export default function PWAUpdatePrompt() {
  usePWA();
  return null;
}
