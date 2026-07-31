import { useRouteError } from 'react-router-dom';
import { RefreshCw, AlertTriangle } from 'lucide-react';
import { isStaleChunkError, forceFreshReload } from '../utils/staleChunkReload';

/**
 * Root `errorElement` for the router. Without one, any render/loader error —
 * most commonly "Failed to fetch dynamically imported module" right after a
 * deploy (the open tab's old index.html points at hashed chunks that no
 * longer exist) — falls through to React Router's built-in developer error
 * screen ("Hey developer 👋"), which is what users were seeing mid-deploy.
 *
 * Deliberately context-free: this renders OUTSIDE every provider (Auth, i18n,
 * Toast…), so it must not call their hooks — that's also why it doesn't reuse
 * `ErrorPage` (which needs AuthContext). English-only copy is acceptable here
 * for the same reason.
 *
 * For stale-chunk errors the primary action routes through
 * `forceFreshReload()` — a plain reload can be served the PRECACHED old app
 * shell by the service worker and "heal" onto the previous build.
 */
export default function RouterErrorPage() {
  const error = useRouteError();
  const message =
    error instanceof Error
      ? error.message
      : typeof error === 'string'
        ? error
        : JSON.stringify(error ?? 'Unknown error');
  const stale = isStaleChunkError(message);

  const title = stale ? 'A new version is available' : 'Something went wrong';
  const description = stale
    ? 'The app was updated while this page was open. Refresh to load the latest version.'
    : 'An unexpected error occurred while rendering this page.';
  const cta = stale ? 'Refresh Now' : 'Reload Page';

  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas px-6">
      <div className="max-w-md w-full text-center">
        <div className="mx-auto w-14 h-14 rounded-2xl bg-island-2 border border-line flex items-center justify-center mb-5">
          {stale ? (
            <RefreshCw size={26} className="text-[var(--accent-text)]" />
          ) : (
            <AlertTriangle size={26} className="text-warn" />
          )}
        </div>
        <h1 className="text-lg font-semibold text-content">{title}</h1>
        <p className="mt-2 text-sm text-content-3">{description}</p>
        <div className="mt-6 flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => forceFreshReload()}
            className="px-4 py-2 rounded-lg bg-[var(--accent)] hover:opacity-90 text-white text-sm font-medium transition-opacity"
          >
            {cta}
          </button>
          {!stale && (
            <a
              href="/"
              className="px-4 py-2 rounded-lg border border-line text-sm text-content-2 hover:bg-island-2 transition-colors"
            >
              Go Home
            </a>
          )}
        </div>
        {!stale && (
          <p className="mt-6 text-[11px] text-content-4 break-all max-h-24 overflow-y-auto">
            {message}
          </p>
        )}
      </div>
    </div>
  );
}
