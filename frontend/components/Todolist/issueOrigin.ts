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

/** Content surfaces that can spawn an issue. Add a case here + a writer.
 *  'pipeline' is not a content surface — it marks a content-relay step child
 *  (W2b, origin_id `pipeline:{run}:{step}`); it renders a module chip but has
 *  no back-link target (the parent issue id is not carried in origin_id). */
export type OriginContentKind =
  | 'canvas'
  | 'scene'
  | 'project_stage'
  | 'publish'
  | 'pipeline';

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
  // Everything after the FIRST colon is the id. For 'project_stage' the id is
  // itself `{projectId}:{stageId}` (a second colon) — kept as a string, never
  // split into numbers, so Snowflake bigints survive intact.
  const id = originId.slice(idx + 1);
  if (!id) return null;
  if (
    kind !== 'canvas' &&
    kind !== 'scene' &&
    kind !== 'project_stage' &&
    kind !== 'publish' &&
    kind !== 'pipeline'
  )
    return null;
  return { kind, id };
}

/** Route to the content a parsed origin points at. Returns null when the origin
 *  has no navigable target (a pipeline step child — its parent issue id is not
 *  in origin_id, so there is nothing to link to). */
export function originPath(
  origin: ParsedOrigin,
  teamId: string | undefined,
): string | null {
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
    case 'project_stage': {
      // id is `{projectId}:{stageId}` — the project detail route only needs the
      // projectId (stageId is carried for the reverse lookup, not routing).
      // Split on the first colon; keep it a string (Snowflake bigint).
      const projectId = origin.id.split(':')[0];
      if (!teamId || !projectId) return '/projects';
      return `/team/${teamId}/projects/${projectId}`;
    }
    case 'publish':
      // The distribution surface lists batches; a per-batch deep link doesn't
      // exist yet, so land on the module (same don't-over-design call as
      // scene→Projects).
      return teamId ? `/team/${teamId}/distribution` : '/distribution';
    case 'pipeline':
      // A pipeline step child's origin_id is `pipeline:{run}:{step}` — the
      // parent issue id is NOT in it, so there is no sensible landing. Return
      // null; the Related tab renders the origin label without a link.
      return null;
  }
}

/** Human label for the origin row. */
export function originLabel(origin: ParsedOrigin): string {
  switch (origin.kind) {
    case 'canvas':
      return 'From a canvas';
    case 'scene':
      return 'From a script scene';
    case 'project_stage':
      return 'From a project stage';
    case 'publish':
      return 'From a publish batch';
    case 'pipeline':
      return 'From a pipeline step';
  }
}

/** Compact module chip for list/board rows (the mockup's per-row tag).
 *  Derived from the origin — the row's honest module context — because a
 *  labels schema doesn't exist. Rows without a content origin get no chip,
 *  exactly like the mockup's untagged rows. Dot colors ride existing
 *  tailwind tokens (no new palette).
 *
 *  `originKind` is the issue's origin_kind column. A routine-created issue
 *  ('routine') stores a bare schedule id in origin_id (not a content ref), so
 *  it can't be chipped from origin_id alone — the kind gives it a visible,
 *  filterable "Autopilot" trace regardless. */
export function originModule(
  originId: string | null | undefined,
  originKind?: string | null,
): { label: string; dotClass: string } | null {
  if (originKind === 'routine') {
    return { label: 'Autopilot', dotClass: 'bg-amber-400' };
  }
  const origin = parseOriginId(originId);
  if (!origin) return null;
  switch (origin.kind) {
    case 'canvas':
      return { label: 'Canvas', dotClass: 'bg-sky-400' };
    case 'scene':
      return { label: 'Script', dotClass: 'bg-violet-400' };
    case 'project_stage':
      return { label: 'Project', dotClass: 'bg-emerald-400' };
    case 'publish':
      return { label: 'Publish', dotClass: 'bg-rose-400' };
    case 'pipeline':
      return { label: 'Pipeline', dotClass: 'bg-cyan-400' };
  }
}
