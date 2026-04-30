/**
 * DBOS workflow → UnifiedTask shape mapping (D8-1).
 *
 * The Task Center UI was built around the legacy unified_tasks row
 * shape. Rather than rewrite every consumer (TopBar, TasksPanel,
 * ResourcesContext, etc), we adapt DBOS workflow rows into the same
 * UnifiedTask shape on read. Fields DBOS doesn't carry (progress,
 * subtitle, group_id, dedup_key, subscribers, error_code) are
 * defaulted; progress is filled in by the Redis WebSocket channel
 * exactly as before.
 */

import type {
  DbosWorkflowSnapshot,
  DbosWorkflowStatus,
} from '../services/dbosWorkflowService';
import type {
  TaskStatus,
  TaskType,
  UnifiedTask,
} from './TaskManagerContext';

/** Raw row shape from Supabase Realtime on dbos.workflow_status. */
export interface DbosWorkflowStatusRow {
  workflow_uuid: string;
  status: string;
  name: string | null;
  authenticated_user: string | null;
  created_at: number | string | null;
  updated_at: number | string | null;
  started_at_epoch_ms: number | null;
  workflow_deadline_epoch_ms: number | null;
  error: string | null;
  inputs?: string | null; // JSONB-serialized DBOS workflow input (when load_input)
}

/** Map workflow function name → UI task_type. */
export function workflowNameToTaskType(name: string | null | undefined): TaskType {
  if (!name) return 'parse'; // unknown — fallback to a sensible default
  // Strip "_workflow" suffix for matching
  const normalized = name.replace(/_workflow$/, '');
  switch (normalized) {
    case 'parse':
      return 'parse';
    case 'download':
      return 'download';
    case 'transcode':
      return 'transcode';
    case 'analyze_l1':
    case 'analyze_l2':
    case 'visual_analysis':
      return 'ai_extract';
    case 'ai_summary':
    case 'summary':
      return 'ai_summary';
    case 'ai_transcription':
    case 'transcription':
      return 'ai_transcription';
    default:
      // Storyboard / scheduled / memory / sweeper etc — bucket as ai_pipeline
      // so they show up in the AI category rather than vanishing.
      return 'ai_pipeline';
  }
}

export function dbosStatusToTaskStatus(
  s: DbosWorkflowStatus | string | null | undefined
): TaskStatus {
  switch (s) {
    case 'PENDING':
    case 'ENQUEUED':
      return 'pending';
    case 'RUNNING':
      return 'processing';
    case 'SUCCESS':
      return 'completed';
    case 'ERROR':
    case 'RETRIES_EXCEEDED':
    case 'MAX_RECOVERY_ATTEMPTS_EXCEEDED':
      return 'failed';
    case 'CANCELLED':
      return 'cancelled';
    default:
      return 'pending';
  }
}

function toIso(value: number | string | null | undefined): string | undefined {
  if (value == null) return undefined;
  if (typeof value === 'number') return new Date(value).toISOString();
  return value;
}

/** DBOS persists `error` as a pickled exception (BYTEA in PG → base64
 * string over Supabase Realtime). The REST endpoints (`/api/v1/workflows`)
 * stringify it via _stringify_error on the backend, but Realtime push
 * delivers the raw byte payload. Detect the pickle protocol prefix and
 * collapse it to a friendly placeholder so the Task Center doesn't
 * display 200 chars of base64 in the error field. The detail drawer
 * (which hits /status) will show the proper exception text. */
function friendlyError(raw: string | null | undefined): string | undefined {
  if (!raw) return undefined;
  // Pickle protocols 4/5 base64-encode to strings starting with `gAS`
  // (proto 4) or `gASV` (proto 5). 100-char threshold avoids matching
  // legitimate error messages that happen to contain `gAS`.
  if (raw.length > 100 && /^gAS[A-Za-z0-9+/=]*$/.test(raw.slice(0, 60))) {
    return 'Workflow failed — open the detail panel to see the error.';
  }
  return raw;
}

/** Parse a JSON-or-already-object input value defensively. */
function parseInput(raw: unknown): Record<string, unknown> {
  if (!raw) return {};
  if (typeof raw === 'object') return (raw as Record<string, unknown>) || {};
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      return typeof parsed === 'object' && parsed ? (parsed as Record<string, unknown>) : {};
    } catch {
      return {};
    }
  }
  return {};
}

/** DBOS input is `{ args: [...], kwargs: {...} }`. Pull a known kwarg. */
function pickKwarg(input: Record<string, unknown>, key: string): unknown {
  const kwargs = input.kwargs as Record<string, unknown> | undefined;
  if (kwargs && key in kwargs) return kwargs[key];
  // Some workflows pass positional args; fall back to scanning args[0] if it's a dict
  const args = input.args as unknown[] | undefined;
  if (Array.isArray(args) && args.length > 0 && typeof args[0] === 'object' && args[0]) {
    const first = args[0] as Record<string, unknown>;
    if (key in first) return first[key];
  }
  return undefined;
}

function deriveTitle(name: string | null | undefined, input: Record<string, unknown>): string {
  const taskType = workflowNameToTaskType(name);
  const url = pickKwarg(input, 'url');
  const platformId = pickKwarg(input, 'platform_id');
  const mediaId = pickKwarg(input, 'media_id');
  const videoTitle = pickKwarg(input, 'video_title');
  switch (taskType) {
    case 'parse':
      return typeof url === 'string' ? `Parse ${url.slice(0, 60)}` : 'Parse';
    case 'download':
      if (typeof videoTitle === 'string' && videoTitle) return `Download ${videoTitle.slice(0, 60)}`;
      if (typeof platformId === 'string') return `Download ${platformId}`;
      return 'Download';
    case 'transcode':
      return typeof mediaId !== 'undefined' ? `Transcode ${mediaId}` : 'Transcode';
    case 'ai_extract':
      return typeof mediaId !== 'undefined' ? `Analyze ${mediaId}` : 'Analyze';
    case 'ai_summary':
      return typeof mediaId !== 'undefined' ? `Summary ${mediaId}` : 'Summary';
    case 'ai_transcription':
      return typeof mediaId !== 'undefined' ? `Transcribe ${mediaId}` : 'Transcribe';
    default:
      return name || 'Task';
  }
}

interface MapOptions {
  /** Fallback user_id when row.authenticated_user is null (rare). */
  fallbackUserId?: string;
}

/** Convert a REST snapshot (GET /api/v1/workflows) into UnifiedTask. */
export function dbosSnapshotToUnifiedTask(
  snap: DbosWorkflowSnapshot,
  opts: MapOptions = {}
): UnifiedTask | null {
  if (!snap.workflow_id) return null;
  const input = parseInput(snap.input);
  const taskType = workflowNameToTaskType(snap.name);
  const status = dbosStatusToTaskStatus(snap.status);
  const createdAtIso = toIso(snap.created_at) || new Date().toISOString();
  const updatedAtIso = toIso(snap.updated_at);
  const isTerminal = status === 'completed' || status === 'failed' || status === 'cancelled';
  return {
    id: snap.workflow_id,
    user_id: snap.authenticated_user || opts.fallbackUserId || '',
    task_type: taskType,
    status,
    title: deriveTitle(snap.name, input),
    progress: status === 'completed' ? 100 : 0,
    error_msg: friendlyError(snap.error),
    media_id:
      (pickKwarg(input, 'media_id') as string | undefined) ??
      (pickKwarg(input, 'platform_id') as string | undefined),
    resource_id: pickKwarg(input, 'resource_id') as string | undefined,
    celery_task_id: snap.workflow_id,
    metadata: { dbos_name: snap.name, queue_name: snap.queue_name },
    created_at: createdAtIso,
    started_at: undefined, // status row carries no start timestamp; SSE/Realtime will fill in
    completed_at: isTerminal ? updatedAtIso : undefined,
    updated_at: updatedAtIso,
  };
}

/** Convert a Realtime row (dbos.workflow_status) into UnifiedTask. */
export function dbosRowToUnifiedTask(
  row: DbosWorkflowStatusRow,
  opts: MapOptions = {}
): UnifiedTask | null {
  if (!row.workflow_uuid) return null;
  const input = parseInput(row.inputs);
  const taskType = workflowNameToTaskType(row.name);
  const status = dbosStatusToTaskStatus(row.status);
  const createdAtIso = toIso(row.created_at) || new Date().toISOString();
  const updatedAtIso = toIso(row.updated_at);
  const startedAtIso = row.started_at_epoch_ms
    ? new Date(row.started_at_epoch_ms).toISOString()
    : undefined;
  const isTerminal = status === 'completed' || status === 'failed' || status === 'cancelled';
  return {
    id: row.workflow_uuid,
    user_id: row.authenticated_user || opts.fallbackUserId || '',
    task_type: taskType,
    status,
    title: deriveTitle(row.name, input),
    progress: status === 'completed' ? 100 : 0,
    error_msg: friendlyError(row.error),
    media_id:
      (pickKwarg(input, 'media_id') as string | undefined) ??
      (pickKwarg(input, 'platform_id') as string | undefined),
    resource_id: pickKwarg(input, 'resource_id') as string | undefined,
    celery_task_id: row.workflow_uuid,
    metadata: { dbos_name: row.name },
    created_at: createdAtIso,
    started_at: startedAtIso,
    completed_at: isTerminal ? updatedAtIso : undefined,
    updated_at: updatedAtIso,
  };
}
