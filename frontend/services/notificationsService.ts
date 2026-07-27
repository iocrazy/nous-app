/**
 * Inbox notifications service (W3d) — client for the narrow notification inbox
 * (`/api/v1/inbox`). Distinct from the legacy broadcast `notificationService.ts`
 * (system/team announcements). All ids are strings (snowflake precision-safe).
 */

import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';

export type InboxKind =
  | 'generation_result'
  | 'publish_result'
  | 'autopilot_output'
  | 'workflow_stage';
export type InboxSeverity = 'info' | 'success' | 'error';
export type InboxLinkKind = 'issue' | 'resource' | 'publish_batch';

export interface InboxNotification {
  id: string;
  kind: InboxKind;
  title: string;
  body?: string | null;
  severity: InboxSeverity;
  link_kind?: InboxLinkKind | null;
  link_id?: string | null;
  team_id?: string | null;
  read: boolean;
  read_at?: string | null;
  created_at?: string | null;
}

export interface InboxListResult {
  notifications: InboxNotification[];
  total: number;
  unread_count: number;
}

const base = () => `${getApiUrl()}/api/v1/inbox`;

export interface ListInboxOptions {
  unreadOnly?: boolean;
  limit?: number;
  offset?: number;
}

export async function listInbox(opts: ListInboxOptions = {}): Promise<InboxListResult> {
  const params = new URLSearchParams();
  if (opts.unreadOnly) params.set('unread_only', 'true');
  if (opts.limit != null) params.set('limit', String(opts.limit));
  if (opts.offset != null) params.set('offset', String(opts.offset));
  const qs = params.toString();

  const res = await fetch(`${base()}${qs ? `?${qs}` : ''}`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });
  if (!res.ok) {
    throw new Error(`Failed to load inbox (${res.status})`);
  }
  return res.json();
}

export async function markInboxRead(id: string): Promise<void> {
  const res = await fetch(`${base()}/${encodeURIComponent(id)}/read`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  if (!res.ok) {
    throw new Error(`Failed to mark notification read (${res.status})`);
  }
}

export async function markAllInboxRead(): Promise<void> {
  const res = await fetch(`${base()}/read-all`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  if (!res.ok) {
    throw new Error(`Failed to mark all read (${res.status})`);
  }
}
