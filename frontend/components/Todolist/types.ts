/**
 * Paperclip-style /todolist UI types (A8).
 *
 * Sources of truth:
 *   IssueStatus / IssuePriority — services/issuesService.ts (= public.issues)
 *   IssueMessage — services/issueMessageService.ts (= public.issue_messages)
 *
 * UI-only shapes (UiIssue, AgentRef, ProjectRef) wrap the backend rows
 * with denormalised display fields (assignee name + initials, project
 * label + color) so list/detail components don't need to chase ID
 * lookups in render code.
 */

export type {
  IssueStatus, IssuePriority, IssueOriginKind, Issue as RawIssue,
} from '../../services/issuesService';
export type {
  IssueMessage, IssueMessageKind, IssueMessageList,
} from '../../services/issueMessageService';

export interface AgentRef {
  id: string;
  slug: string;
  name: string;
  avatar_color?: string;
}

export interface ProjectRef {
  id: number;
  name: string;
  color?: string;
}

import type { Issue as RawIssue, IssueStatus, IssuePriority } from '../../services/issuesService';

/**
 * UI-shape Issue: raw backend Issue + denormalised display fields.
 * The list/detail components consume this; the page-level wrapper builds
 * it via lookup maps fetched once on mount.
 */
export interface UiIssue {
  id: number;
  identifier: string;
  title: string;
  description: string | null;
  status: IssueStatus;
  priority: IssuePriority;
  assignee?: AgentRef;
  /** When assigned to a human user (not an agent). */
  assignee_user_label?: string;
  project?: ProjectRef;
  parent_id: number | null;
  created_at: string;
  updated_at: string;
  /** Display value for list rows. Falls back to updated_at, then created_at. */
  last_activity_at: string;
  /** Original raw row, in case detail panes need extra fields. */
  raw: RawIssue;
}
