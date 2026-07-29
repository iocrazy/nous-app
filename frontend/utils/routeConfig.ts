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
  // Topic Inspiration / 话题灵感 — the app's home/default page, routed at
  // /parser (also pathnameToView's catch-all fallback; see its comment —
  // every other view has its own explicit prefix match, so in practice this
  // only fires for the real /parser route).
  parser: 'inspiration',
  // Resources + Distribution both read as 钢蓝 per spec §5.
  resources: 'resources',
  distribution: 'resources',
};

export function viewToModule(view: ViewState): ModuleAccent | null {
  return VIEW_TO_MODULE[view] ?? null;
}
