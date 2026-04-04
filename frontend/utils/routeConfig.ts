import { ViewState } from '../types';

// ---------------------------------------------------------------------------
// Centralized route mapping — single source of truth for URL ↔ ViewState
// ---------------------------------------------------------------------------

/**
 * Maps each ViewState to its canonical URL path (without team prefix).
 * Used for navigation and active-view highlighting.
 */
export const VIEW_PATH_MAP: Record<ViewState, string> = {
  parser: '/parser',
  dashboard: '/dashboard',
  settings: '/settings',
  cleanup: '/cleanup',
  mediatrack: '/projects',
  points: '/points',
  billing: '/billing',
  members: '/members',
  resources: '/resources',
  todolist: '/todolist',
  shared: '/shared',
};

/**
 * Derives the active ViewState from a URL pathname.
 * Supports both /team/:teamId/:view and legacy /:view patterns.
 */
export function pathnameToView(pathname: string): ViewState {
  // Strip /team/:teamId/ prefix if present
  const stripped = pathname.replace(/^\/team\/[^/]+/, '');

  // PlayerPage: always maps to resources view
  if (stripped.startsWith('/player/')) {
    return 'resources';
  }

  if (stripped.startsWith('/library')) return 'resources'; // legacy /library → resources
  if (stripped.startsWith('/dashboard')) return 'dashboard';
  if (stripped.startsWith('/settings')) return 'settings';
  if (stripped.startsWith('/cleanup')) return 'cleanup';
  if (stripped.startsWith('/projects')) return 'mediatrack';
  if (stripped.startsWith('/points')) return 'points';
  if (stripped.startsWith('/billing')) return 'billing';
  if (stripped.startsWith('/members')) return 'members';
  if (stripped.startsWith('/resources')) return 'resources';
  if (stripped.startsWith('/todolist')) return 'todolist';
  if (stripped.startsWith('/shared')) return 'shared';

  return 'parser';
}
