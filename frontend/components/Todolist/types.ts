/**
 * Paperclip-style Issue/Todo types (A8 UI scaffold).
 *
 * Wire shape kept close to paperclip's `Issue` so the swap to
 * mediahub's `issues` table (mig 166) + new `issue_messages` table is
 * mostly a service-layer rewrite, not a component-layer rewrite.
 */

export type IssueStatus =
  | 'backlog'
  | 'todo'
  | 'in_progress'
  | 'in_review'
  | 'blocked'
  | 'done'
  | 'cancelled';

export type IssuePriority = 'no_priority' | 'urgent' | 'high' | 'medium' | 'low';

export interface AgentRef {
  id: string;
  slug: string;
  name: string;
  avatar_color?: string;
}

export interface ProjectRef {
  id: string;
  name: string;
  color?: string;
}

export interface Issue {
  id: string;
  /** Short identifier shown in UI, e.g. MED-3. */
  identifier: string;
  title: string;
  description?: string;
  status: IssueStatus;
  priority: IssuePriority;
  project?: ProjectRef;
  assignee?: AgentRef;
  parent_id?: string | null;
  created_at: string;
  updated_at: string;
  team_id: string;
  /** Last activity timestamp shown in list rows. */
  last_activity_at: string;
}

export type IssueMessageKind = 'comment' | 'agent_run' | 'system_status';

export interface IssueMessage {
  id: string;
  issue_id: string;
  kind: IssueMessageKind;
  /** Author for comments / agent_run; null for system_status. */
  author?: AgentRef | { kind: 'user'; id: string; name: string };
  /** Markdown body for comment / agent_run summary. */
  body?: string;
  /** Optional structured payload (status change, run metadata, etc). */
  meta?: Record<string, unknown>;
  /** When kind=agent_run, how long the agent worked. */
  duration_seconds?: number;
  /** When kind=system_status, the status transition. */
  from_status?: IssueStatus;
  to_status?: IssueStatus;
  created_at: string;
}
