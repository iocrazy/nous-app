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
  agents: '/agents',
  skills: '/skills',
  ailibrary: '/ai-library/agents',
  chat: '/chat',
  distribution: '/distribution/accounts',
  canvas: '/canvas',
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
  // /ai-library/* is the new nested AI Library area — checked before the
  // flat /agents and /skills so it wins on match.
  if (stripped.startsWith('/ai-library')) return 'ailibrary';
  if (stripped.startsWith('/distribution')) return 'distribution';
  // Matches both the list (/canvas) and the editor (/canvas/:canvasId).
  if (stripped.startsWith('/canvas')) return 'canvas';
  if (stripped.startsWith('/agents')) return 'agents';
  if (stripped.startsWith('/skills')) return 'skills';
  if (stripped.startsWith('/chat')) return 'chat';

  return 'parser';
}

// ---------------------------------------------------------------------------
// K1.5 (2026-07-29) — per-module accent scoping (docs/superpowers/specs/
// 2026-07-29-warm-paper-palette-design.md §5). AppLayout stamps the resolved
// module onto <html data-module="…">; index.css's `[data-module="x"]` blocks
// then re-point the accent ladders (indigo/violet/emerald/green) to that
// module's primary hue. Colocated with `pathnameToView` since it's a direct
// function of the same ViewState this file already owns as the single
// source of truth — do not build a second route→module map elsewhere.
// ---------------------------------------------------------------------------

export type ModuleAccent = 'ai' | 'inspiration' | 'resources';

/**
 * Views with a dedicated non-green module accent. Every other ViewState
 * (mediatrack/projects included — projects IS the global green default, so
 * it needs no override) resolves to `null`, meaning "no data-module
 * attribute" → falls through to the global accent ladder.
 */
const VIEW_TO_MODULE: Partial<Record<ViewState, ModuleAccent>> = {
  // AI Library / Agent area — nested /ai-library/* plus the flat legacy
  // /agents, /skills, /chat views that cover the same Agent/AI surface.
  ailibrary: 'ai',
  agents: 'ai',
  skills: 'ai',
  chat: 'ai',
  // 'parser' deliberately NOT listed here — see the dedicated pathname guard
  // in viewToModule() below (K1.5 review minor-fix).
  // Resources + Distribution both read as 钢蓝 per spec §5.
  resources: 'resources',
  distribution: 'resources',
};

/**
 * @param pathname Raw `location.pathname` (same input as `pathnameToView`).
 *   Required, not optional: see the 'parser' guard below — without the raw
 *   pathname there is no way to tell a genuine Topic Inspiration visit apart
 *   from `pathnameToView`'s catch-all.
 */
export function viewToModule(view: ViewState, pathname: string): ModuleAccent | null {
  if (view === 'parser') {
    // `pathnameToView`'s trailing `return 'parser'` is BOTH the real /parser
    // route's result AND the fallback for any route matching no explicit
    // prefix at all (see its comment) — a genuinely unrecognized path would
    // silently inherit the inspiration (ochre) accent otherwise (K1.5 review
    // minor finding). Only tint when the URL actually is /parser; every other
    // fallthrough defaults to null (no override → global green), same as any
    // other unmapped view.
    const stripped = pathname.replace(/^\/team\/[^/]+/, '');
    return stripped.startsWith('/parser') ? 'inspiration' : null;
  }
  return VIEW_TO_MODULE[view] ?? null;
}
