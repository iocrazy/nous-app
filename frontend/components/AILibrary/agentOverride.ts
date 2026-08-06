// Personal-override predicates for system-preset agents (migration 341).
//
// Editing a system preset does NOT edit the template: the backend routes the
// seven overridable fields (identity_md / soul_md / agent_md / model /
// temperature / max_tokens / fallback_models) into a per-caller layer and
// returns the MERGED agent, annotated with `override_scope` (which layer won)
// and `override_fields` (which columns it replaced).
//
// The single-agent GET merges the USER layer only — it never passes a team id
// — so anything the editor sees is the caller's own layer, resettable via
// DELETE /agents/{slug}/override with the default scope=user. Team-scoped
// overrides exist server-side but are not reachable from this surface.

import type { AILibraryAgent } from '../../types';

/** How many fields the caller's override layer replaced (0 when absent). */
export function overrideFieldCount(agent: AILibraryAgent): number {
  return agent.override_fields?.length ?? 0;
}

/**
 * Does this agent carry a personal override the caller can reset?
 *
 * Guarded on `is_system_preset` as well as the field list: a user-owned agent
 * IS the row being edited, so there is no system default to fall back to and
 * a reset would have nothing to delete.
 */
export function hasPersonalOverride(agent: AILibraryAgent): boolean {
  return Boolean(agent.is_system_preset) && overrideFieldCount(agent) > 0;
}
