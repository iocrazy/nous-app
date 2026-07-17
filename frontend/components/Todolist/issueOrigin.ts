/**
 * Content → issue back-links.
 *
 * When you turn a piece of content into a to-do, `origin_kind` stays 'manual'
 * (a person clicked it — which is what 'manual' means) and the content it came
 * from is recorded in `origin_id` as `{kind}:{id}`. That needs no schema
 * change: origin_id is free TEXT and already carries ids this way (routines
 * store their schedule id there), and issues_origin_idx covers
 * (origin_kind, origin_id) for the reverse lookup.
 *
 * This module owns the format so the writers (content menus) and the reader
 * (the issue's Related tab) can't drift apart.
 */

/** Content surfaces that can spawn an issue. Add a case here + a writer. */
export type OriginContentKind = 'canvas' | 'scene';

export interface ParsedOrigin {
  kind: OriginContentKind;
  id: string;
}

/** Build the origin_id a content surface should stamp on a new issue. */
export function buildOriginId(kind: OriginContentKind, id: string): string {
  return `${kind}:${id}`;
}

/**
 * Parse an issue's origin_id back into its content reference. Returns null for
 * origins that aren't content-backed (a routine's bare schedule id, a null,
 * or a kind we don't render a link for) — callers just skip the back-link.
 */
export function parseOriginId(originId: string | null | undefined): ParsedOrigin | null {
  if (!originId) return null;
  const idx = originId.indexOf(':');
  if (idx <= 0) return null;
  const kind = originId.slice(0, idx);
  const id = originId.slice(idx + 1);
  if (!id) return null;
  if (kind !== 'canvas' && kind !== 'scene') return null;
  return { kind, id };
}

/** Route to the content a parsed origin points at. */
export function originPath(origin: ParsedOrigin, teamId: string | undefined): string {
  switch (origin.kind) {
    case 'canvas':
      return teamId ? `/team/${teamId}/canvas/${origin.id}` : `/canvas/${origin.id}`;
    case 'scene':
      // A scene lives inside a script inside a project, so the fullscreen
      // editor route (team/:teamId/projects/:projectId/scripts/:scriptId) can't
      // be rebuilt from a bare scene id. Per the "don't over-design" call we
      // land on the team's Projects area — where scripts live — rather than
      // stamp a scene→script resolver we don't have. Falls back to the
      // team-agnostic Projects list when the issue view has no team in scope.
      return teamId ? `/team/${teamId}/projects` : `/projects`;
  }
}

/** Human label for the origin row. */
export function originLabel(origin: ParsedOrigin): string {
  switch (origin.kind) {
    case 'canvas':
      return 'From a canvas';
    case 'scene':
      return 'From a script scene';
  }
}
