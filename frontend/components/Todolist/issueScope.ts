/**
 * IssueScope — the single, typed description of *which* issues a list surface
 * is looking at, and the derived rules for querying / creating / filtering /
 * labelling under that scope.
 *
 * Team is a hard boundary: EVERY variant carries `teamId`, so no code path can
 * ever aggregate issues across teams. `project` narrows to one project inside a
 * team; `my` and `agent` are pure client-side projections of the team list
 * (they add NO backend parameter — the team query already fetched the rows, we
 * just filter them here) so the same 200-row page powers all three views.
 *
 * Snowflake discipline: every id stays a STRING end-to-end. `Number()` would
 * round a BIGINT past 2^53 and a rounded filter/create can never match the
 * exact row again — so this module never coerces ids.
 */

import type { UiIssue } from './types';

export type IssueScope =
  | { type: 'team'; teamId: string }
  | { type: 'project'; teamId: string; projectId: string }
  | { type: 'my'; teamId: string; userId: string }
  | { type: 'agent'; teamId: string; agentId: string };

// ── Query filters → listIssues() params ─────────────────────────────
export interface ScopeQueryFilters {
  team_id: string;
  project_id?: string;
}

/**
 * Backend list params for a scope. `my`/`agent` deliberately return only the
 * team filter — their narrowing is client-side (see scopeClientFilter) so we
 * never round-trip for a projection we can compute locally.
 */
export function scopeQueryFilters(scope: IssueScope): ScopeQueryFilters {
  if (scope.type === 'project') {
    return { team_id: scope.teamId, project_id: scope.projectId };
  }
  return { team_id: scope.teamId };
}

// ── Create defaults → NewIssueDialog prefill ────────────────────────
export interface ScopeCreateDefaults {
  team_id: string;
  project_id?: string;
}

/**
 * Fields a new issue created under this scope must inherit. All strings — never
 * Number()'d. `project` pins the new issue to its project; the others create at
 * team level (an unscoped issue that still belongs to the team).
 */
export function scopeCreateDefaults(scope: IssueScope): ScopeCreateDefaults {
  if (scope.type === 'project') {
    return { team_id: scope.teamId, project_id: scope.projectId };
  }
  return { team_id: scope.teamId };
}

// ── Client-side predicate for the already-fetched team list ─────────
/**
 * Row predicate applied on top of the team-scoped fetch.
 *  - `my`    → issues I created OR are assigned to me (the OR naturally dedupes:
 *              each row is tested once, so an issue I both created and own shows
 *              exactly once).
 *  - `agent` → issues whose assignee is this agent.
 *  - `team` / `project` → always true (the backend query already scoped them).
 */
export function scopeClientFilter(scope: IssueScope): (issue: UiIssue) => boolean {
  switch (scope.type) {
    case 'my':
      return (i) =>
        i.raw.created_by_user_id === scope.userId ||
        i.raw.assignee_user_id === scope.userId;
    case 'agent':
      return (i) => i.raw.assignee_agent_id === scope.agentId;
    case 'team':
    case 'project':
    default:
      return () => true;
  }
}

// ── "Creates in …" badge copy ───────────────────────────────────────
export interface ScopeNames {
  teamName?: string;
  projectName?: string;
}

/**
 * Human-readable "where does a new issue land" label for the toolbar badge.
 * Falls back to `Team {id}` / the raw project id when a caller can't resolve a
 * display name. `agent` scope is read-only — it creates nothing.
 *
 *   team    → "Creates in Team 8"
 *   project → "Creates in Neon Short · Team 8"  (or "Creates in 21321 · Team 8")
 *   my      → "Creates in Team 8"
 *   agent   → "Read-only view"
 */
export function scopeCreatesInLabel(scope: IssueScope, names: ScopeNames = {}): string {
  if (scope.type === 'agent') return 'Read-only view';
  const team = names.teamName?.trim() || `Team ${scope.teamId}`;
  if (scope.type === 'project') {
    const project = names.projectName?.trim() || scope.projectId;
    return `Creates in ${project} · ${team}`;
  }
  return `Creates in ${team}`;
}

/** Whether creation affordances (New Issue button, `C` shortcut) should hide. */
export function scopeIsReadOnly(scope: IssueScope): boolean {
  return scope.type === 'agent';
}
