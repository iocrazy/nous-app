/**
 * Shared mappers from backend rows to the UiIssue view-model, used by both the
 * team-level TodolistPage and the in-workspace Tasks module. Kept framework-free
 * so either surface can build its list without duplicating the agent-color hash
 * or the project-name resolution.
 */

import type { AgentRef, UiIssue } from './types';
import type { Issue } from '../../services/issuesService';
import type { AILibraryAgent } from '../../types';

const AGENT_PALETTE = [
  'bg-amber-500', 'bg-emerald-500', 'bg-blue-500', 'bg-purple-500',
  'bg-pink-500', 'bg-cyan-500', 'bg-orange-500', 'bg-indigo-500',
];

export function colorForAgent(slug: string): string {
  let h = 0;
  for (const ch of slug) h = (h * 31 + ch.charCodeAt(0)) & 0xfffff;
  return AGENT_PALETTE[h % AGENT_PALETTE.length];
}

export function toAgentRef(a: AILibraryAgent): AgentRef {
  return {
    id: a.id,
    slug: a.slug,
    name: a.name,
    avatar_color: colorForAgent(a.slug),
  };
}

/** Optional lookup of project id → display name, keyed by stringified id. */
export type ProjectNameMap = Record<string, { name: string; color?: string }>;

export function toUiIssue(
  raw: Issue,
  agentsById: Record<string, AgentRef>,
  projectsById?: ProjectNameMap,
): UiIssue {
  const lastActivity = raw.updated_at ?? raw.created_at;
  const projectMeta = raw.project_id != null
    ? projectsById?.[String(raw.project_id)]
    : undefined;
  return {
    id: raw.id,
    identifier: raw.identifier,
    title: raw.title,
    description: raw.description,
    status: raw.status,
    priority: raw.priority,
    // Fallback ref: an assigned agent missing from agentsById (deleted agent,
    // scope-filtered list) must still render an avatar — the mockup shows the
    // assignee for every assigned row, and silently dropping it reads as
    // "unassigned".
    assignee: raw.assignee_agent_id
      ? agentsById[raw.assignee_agent_id] ?? {
          id: raw.assignee_agent_id,
          slug: 'agent',
          name: 'Agent',
          avatar_color: 'bg-ink-600',
        }
      : undefined,
    assignee_user_label: raw.assignee_user_id ? `User ${raw.assignee_user_id.slice(0, 6)}` : undefined,
    project: raw.project_id != null
      ? { id: raw.project_id, name: projectMeta?.name ?? `Project ${raw.project_id}`, color: projectMeta?.color }
      : undefined,
    parent_id: raw.parent_id ?? null,
    created_at: raw.created_at,
    updated_at: raw.updated_at ?? raw.created_at,
    last_activity_at: lastActivity,
    raw,
  };
}
