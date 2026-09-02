/**
 * Where the canvas gets its asset-library scope from.
 *
 * `/api/v1/assets` is scoped per request (`?scope_id=`, a `teams.id`), and the
 * canvas has no ResourcesContext to inherit one from — that provider is the
 * library pages'. What the canvas DOES have is its route: every canvas URL is
 * `/team/:teamId/canvas/:canvasId`, including a personal workspace's (the bare
 * `/canvas/:id` paths are `RedirectToTeam` entries that bounce to the personal
 * team's snowflake before anything renders). So the URL segment IS the scope,
 * with no team-context round trip and no second copy to drift.
 *
 * `resPath` mirrors `ResourcesContext`'s helper exactly, so a link built here
 * and a link built by the shelf land on the same route.
 *
 * Returns `scopeId: ''` when the route has no team segment. Callers must treat
 * that as "no scope yet" and skip the request — an empty `scope_id` is a 403
 * `not_a_member`, not an unscoped query.
 */

import { useCallback, useMemo } from 'react';
import { useParams } from 'react-router-dom';

export interface CanvasScope {
  /** `teams.id` for every scoped assets call, or '' when the route has none. */
  scopeId: string;
  /** Prefix an app path with the current team segment. */
  resPath: (path: string) => string;
}

export function useCanvasScope(): CanvasScope {
  const { teamId } = useParams<{ teamId?: string }>();
  const resPath = useCallback(
    (path: string) => (teamId ? `/team/${teamId}${path}` : path),
    [teamId],
  );
  return useMemo(() => ({ scopeId: teamId ?? '', resPath }), [teamId, resPath]);
}

/** The `/team/:teamId` segment of a canvas URL, or '' when there is none. */
export function scopeIdFromPathname(pathname: string): string {
  return /^\/team\/([^/?#]+)(?:[/?#]|$)/.exec(pathname ?? '')?.[1] ?? '';
}

/**
 * The same scope, for the RUN path — which is not React.
 *
 * `chainRun` / `loopRun` / `regenerate` build their generation runners in plain
 * modules, so they cannot call the hook, and threading a scope down from every
 * component that might start a run would put a second copy of this answer in
 * four places. Reading the location instead keeps one definition: the URL IS
 * the scope, which is the whole premise of this file.
 *
 * The parse is deliberately the same shape the router matches (`/team/:teamId`
 * at the root, nothing else), and `useCanvasScope` and this function are pinned
 * against each other by `canvasScope.test.tsx` so they cannot answer a canvas
 * URL differently.
 */
export function currentCanvasScopeId(): string {
  if (typeof window === 'undefined') return '';
  return scopeIdFromPathname(window.location.pathname);
}
