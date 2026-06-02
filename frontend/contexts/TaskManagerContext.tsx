import React, { createContext, useContext, useReducer, useEffect, useCallback, useRef } from 'react';
import { getSupabaseClient, getSupabaseAccessToken } from '../supabaseClient';
import { useAuth } from './AuthContext';
import { getAuthHeaders } from '../services/parserService';
import {
  cancelWorkflow,
  restartWorkflow,
} from '../services/dbosWorkflowService';

// ─── Types ──────────────────────────────────────────────

export type TaskType =
  | 'parse'
  | 'download'
  | 'upload'
  | 'transcode'
  | 'ai_pipeline'
  | 'ai_extract'
  | 'ai_transcription'
  | 'ai_summary'
  // Agent execution (chat / issue turns) — sourced from agent_runs, not
  // task_tracking; merged into the Task Center view client-side.
  | 'agent';

export type TaskCategory = 'transfer' | 'processing' | 'ai';

export type TaskStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled';

export type TaskPhase = 'queued' | 'dedup_check' | 'processing' | 'completed' | 'failed' | 'cancelled';

/** Row shape of public.task_tracking after the D8-A migration. PK is
 * dbos_workflow_id (UUID string == dbos.workflow_status.workflow_uuid).
 * Kept the type name `UnifiedTask` for backwards-compat with the many
 * importers across components — it's the same data, just a renamed
 * underlying table. */
export interface UnifiedTask {
  /** Primary key = DBOS workflow_id (UUID string). */
  id: string;
  /** Alias of `id` — historically separate when celery_task_id and PK
   * differed. After D8-A they're the same value; keep for compat. */
  dbos_workflow_id?: string;
  /** @deprecated D7 alias of dbos_workflow_id; will be removed. */
  celery_task_id?: string;
  user_id: string;
  task_type: TaskType;
  status: TaskStatus;
  title: string;
  subtitle?: string;
  progress: number;
  speed?: number;
  total_bytes?: number;
  error_msg?: string;
  resource_id?: string;
  media_id?: string;
  /** @deprecated Use media_id instead */
  video_id?: string;
  group_id?: string;
  /** Parent task_flows row id — links sibling tasks of one user
   * submission (parse → download → transcode/extract_audio/ai_*) so the
   * UI can render them as one chain. NULL for legacy / standalone tasks. */
  flow_id?: string;
  metadata: Record<string, unknown>;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  updated_at?: string;
  phase?: TaskPhase;
  dedup_key?: string;
  subscribers?: Array<{ user_id: string; resource_id: string; subscribed_at: string }>;
  error_code?: string;
  cost_cents?: number;
}

/** Map task_type to its high-level category */
export function getTaskCategory(type: TaskType): TaskCategory {
  switch (type) {
    case 'parse':
    case 'download':
    case 'upload':
      return 'transfer';
    case 'transcode':
      return 'processing';
    case 'ai_pipeline':
    case 'ai_extract':
    case 'ai_transcription':
    case 'ai_summary':
    case 'agent':
      return 'ai';
    default:
      return 'processing';
  }
}

/** Check if a task type is an AI sub-task */
export function isAISubTask(type: TaskType): boolean {
  return type === 'ai_extract' || type === 'ai_transcription' || type === 'ai_summary';
}

export interface TaskManagerState {
  tasks: UnifiedTask[];
  isLoading: boolean;
  /** Supabase Realtime channel (task_tracking postgres_changes) subscribed? */
  isConnected: boolean;
  /** Redis WebSocket (/ws/task-progress for fine-grained download progress) open? */
  isWsConnected: boolean;
}

interface TaskManagerContextType extends TaskManagerState {
  activeTasks: UnifiedTask[];
  totalActive: number;
  activeCounts: Record<TaskType, number>;
  cancelTask: (taskId: string) => Promise<void>;
  retryTask: (taskId: string) => Promise<void>;
  deleteTask: (taskId: string) => Promise<void>;
  clearCompleted: () => Promise<void>;
  refreshTasks: () => Promise<void>;
}

// ─── Reducer ────────────────────────────────────────────

/** Progress update from Redis WebSocket */
export interface WsProgressPayload {
  unified_task_id?: string;
  celery_task_id?: string;
  status: string;
  percent: number;
  speed?: string;
  downloaded?: number;
  total?: number;
  error?: string;
}

/** download_started event from Redis WebSocket (published by backend after dispatching download) */
export interface WsDownloadStartedPayload {
  type: 'download_started';
  unified_task_id?: string;
  celery_task_id: string;
  media_id: string;
  status: string;
  percent: number;
}

type Action =
  | { type: 'SET_TASKS'; tasks: UnifiedTask[] }
  | { type: 'INSERT'; task: UnifiedTask }
  | { type: 'UPDATE'; task: UnifiedTask }
  | { type: 'UPDATE_PROGRESS'; payload: WsProgressPayload }
  | { type: 'DOWNLOAD_STARTED'; payload: WsDownloadStartedPayload }
  | { type: 'DELETE'; id: string }
  | { type: 'SET_LOADING'; loading: boolean }
  | { type: 'SET_CONNECTED'; connected: boolean }
  | { type: 'SET_WS_CONNECTED'; connected: boolean };

/** task_tracking row → UnifiedTask. The table has no `id` column
 * anymore (PK = dbos_workflow_id, see migration 180), so adapt by
 * aliasing PK as `id` for downstream consumers (TopBar, TasksPanel, etc)
 * that still spell it `task.id`. */
function rowToTask(row: Record<string, unknown>): UnifiedTask {
  const pk = (row.dbos_workflow_id || row.id || '') as string;
  return {
    ...(row as unknown as UnifiedTask),
    id: pk,
    dbos_workflow_id: pk,
    celery_task_id: pk, // legacy alias still read by some components
  };
}

/** Map WebSocket status strings to task lifecycle status. */
function wsStatusToTaskStatus(wsStatus?: string): TaskStatus | undefined {
  switch (wsStatus) {
    case 'downloading': return 'processing';
    case 'completed': return 'completed';
    case 'failed': return 'failed';
    default: return undefined;
  }
}

function reducer(state: TaskManagerState, action: Action): TaskManagerState {
  switch (action.type) {
    case 'SET_TASKS':
      return { ...state, tasks: action.tasks, isLoading: false };
    case 'INSERT': {
      // Avoid duplicates by id
      if (state.tasks.some(t => t.id === action.task.id)) {
        return {
          ...state,
          tasks: state.tasks.map(t => t.id === action.task.id ? action.task : t),
        };
      }
      // Also check for placeholder tasks matching by celery_task_id (from DOWNLOAD_STARTED)
      const placeholderIdx = action.task.celery_task_id
        ? state.tasks.findIndex(t => t.celery_task_id === action.task.celery_task_id && t.id.startsWith('ws-dl-'))
        : -1;
      if (placeholderIdx >= 0) {
        return {
          ...state,
          tasks: state.tasks.map((t, i) => i === placeholderIdx ? action.task : t),
        };
      }
      return { ...state, tasks: [action.task, ...state.tasks] };
    }
    case 'UPDATE': {
      // UPDATE merges (vs replaces) so progress/speed pushed by the
      // Redis WebSocket survives a DBOS Realtime status update — DBOS
      // rows don't carry progress, but the WS path keeps it fresh.
      const incoming = action.task;
      const idx = state.tasks.findIndex(t => t.id === incoming.id);
      if (idx < 0) return { ...state, tasks: [incoming, ...state.tasks] };
      const existing = state.tasks[idx];
      const merged: UnifiedTask = {
        ...existing,
        ...incoming,
        // Preserve WS-derived fields when the DBOS update would clobber them with defaults.
        progress: incoming.status === 'completed' ? 100 : (existing.progress || incoming.progress),
        speed: existing.speed ?? incoming.speed,
        total_bytes: existing.total_bytes ?? incoming.total_bytes,
      };
      return {
        ...state,
        tasks: state.tasks.map((t, i) => i === idx ? merged : t),
      };
    }
    case 'UPDATE_PROGRESS': {
      const p = action.payload;
      const mappedStatus = wsStatusToTaskStatus(p.status);
      return {
        ...state,
        tasks: state.tasks.map(t => {
          const match =
            (p.unified_task_id && t.id === p.unified_task_id) ||
            (p.celery_task_id && t.celery_task_id === p.celery_task_id);
          if (!match) return t;
          return {
            ...t,
            progress: p.percent,
            speed: p.speed ? parseSpeedToBytes(p.speed) : t.speed,
            total_bytes: p.total ?? t.total_bytes,
            ...(mappedStatus ? { status: mappedStatus } : {}),
          };
        }),
      };
    }
    case 'DOWNLOAD_STARTED': {
      const ds = action.payload;
      // If task already exists (via Supabase Realtime), update its celery_task_id
      const existing = state.tasks.find(t =>
        (ds.unified_task_id && t.id === ds.unified_task_id) ||
        (ds.celery_task_id && t.celery_task_id === ds.celery_task_id)
      );
      if (existing) {
        return {
          ...state,
          tasks: state.tasks.map(t =>
            t.id === existing.id
              ? { ...t, celery_task_id: ds.celery_task_id, media_id: ds.media_id }
              : t
          ),
        };
      }
      // Insert a placeholder task so useParser can discover it immediately
      const placeholder: UnifiedTask = {
        id: ds.unified_task_id || `ws-dl-${ds.celery_task_id}`,
        user_id: '',
        task_type: 'download',
        status: 'pending',
        title: '',
        progress: 0,
        media_id: ds.media_id,
        celery_task_id: ds.celery_task_id,
        metadata: {},
        created_at: new Date().toISOString(),
      };
      return { ...state, tasks: [placeholder, ...state.tasks] };
    }
    case 'DELETE':
      return { ...state, tasks: state.tasks.filter(t => t.id !== action.id) };
    case 'SET_LOADING':
      return { ...state, isLoading: action.loading };
    case 'SET_CONNECTED':
      return { ...state, isConnected: action.connected };
    case 'SET_WS_CONNECTED':
      return { ...state, isWsConnected: action.connected };
    default:
      return state;
  }
}

// ─── API helpers ────────────────────────────────────────

const API_BASE = 'VITE_API_URL' in import.meta.env
  ? (import.meta.env.VITE_API_URL || '')
  : 'http://localhost:8080';

/** Parse a human-readable speed string (e.g. "2.5 MB/s") into bytes/sec. */
function parseSpeedToBytes(speed: string): number {
  const m = speed.match(/([\d.]+)\s*(B|KB|MB|GB)\/s/i);
  if (!m) return 0;
  const val = parseFloat(m[1]);
  switch (m[2].toUpperCase()) {
    case 'GB': return val * 1073741824;
    case 'MB': return val * 1048576;
    case 'KB': return val * 1024;
    default: return val;
  }
}

/** Derive the WebSocket URL from the API base URL. */
function getWsBaseUrl(): string {
  const base = API_BASE || window.location.origin;
  return base.replace(/^http/, 'ws');
}

async function fetchAllTasks(limit = 200): Promise<UnifiedTask[]> {
  // Read from public.task_tracking via the legacy task-manager REST
  // endpoint (renamed internally to point at task_tracking; URL kept
  // for backwards compat). This table is the application-side sidecar
  // of dbos.workflow_status — DBOS lifecycle is auto-mirrored here by
  // PG trigger (see migration 180), so a single fetch returns everything
  // the UI needs (title/subtitle/progress + status/started_at/error_msg).
  const resp = await fetch(`${API_BASE}/api/v1/task-manager/tasks?limit=${limit}`, {
    headers: await getAuthHeaders(),
  });
  if (!resp.ok) {
    console.error(`[TaskManager] fetchAllTasks failed: ${resp.status} ${resp.statusText}`);
    return [];
  }
  const json = await resp.json();
  const rows: Record<string, unknown>[] = json.data || [];
  return rows.map(rowToTask);
}

async function apiCancelTask(taskId: string): Promise<void> {
  // taskId is the dbos_workflow_id (UUID). Use DBOS-native cancel so the
  // running workflow actually stops. The trigger then mirrors the
  // CANCELLED status into task_tracking automatically.
  try {
    await cancelWorkflow(taskId);
  } catch (e) {
    console.error(`[TaskManager] cancelWorkflow(${taskId}) failed:`, e);
    // Fallback to legacy REST (just flips task_tracking.status without
    // stopping the workflow — last resort).
    await fetch(`${API_BASE}/api/v1/task-manager/tasks/${taskId}/cancel`, {
      method: 'POST',
      headers: await getAuthHeaders(),
    });
  }
}

async function apiRetryTask(taskId: string): Promise<void> {
  // restartWorkflow forks a NEW workflow_id; original row stays in its
  // terminal state. The new workflow's INSERT comes through task_tracking
  // Realtime (router pre-creates the row before DBOS dispatch).
  try {
    await restartWorkflow(taskId);
  } catch (e) {
    console.error(`[TaskManager] restartWorkflow(${taskId}) failed:`, e);
    await fetch(`${API_BASE}/api/v1/task-manager/tasks/${taskId}/retry`, {
      method: 'POST',
      headers: await getAuthHeaders(),
    });
  }
}

async function apiDeleteTask(taskId: string): Promise<void> {
  await fetch(`${API_BASE}/api/v1/task-manager/tasks/${taskId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
}

async function apiClearCompleted(): Promise<void> {
  await fetch(`${API_BASE}/api/v1/task-manager/tasks/clear-completed`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
}

// ─── Context ────────────────────────────────────────────

const TaskManagerContext = createContext<TaskManagerContextType | null>(null);

export const TaskManagerProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { currentUserId } = useAuth();
  const [state, dispatch] = useReducer(reducer, {
    tasks: [],
    isLoading: true,
    isConnected: false,
    isWsConnected: false,
  });
  const channelRef = useRef<ReturnType<ReturnType<typeof getSupabaseClient>['channel']> | null>(null);

  // Initial fetch
  const refreshTasks = useCallback(async () => {
    dispatch({ type: 'SET_LOADING', loading: true });
    try {
      const tasks = await fetchAllTasks();
      dispatch({ type: 'SET_TASKS', tasks });
    } catch (e) {
      console.error('[TaskManager] Failed to fetch tasks:', e);
      dispatch({ type: 'SET_LOADING', loading: false });
    }
  }, []);

  // Supabase Realtime — single source = public.task_tracking.
  // The PG trigger trg_mirror_dbos_lifecycle (migration 180) auto-syncs
  // DBOS lifecycle (status/started_at/completed_at/error_msg) into this
  // table whenever dbos.workflow_status changes. So one channel here
  // delivers BOTH user-facing fields (title/subtitle/progress, written
  // by application code) AND DBOS execution truth (mirrored by trigger).
  // No second dbos.workflow_status subscription needed.
  useEffect(() => {
    if (!currentUserId) {
      console.warn('[TaskManager] No currentUserId, skipping task fetch & subscription');
      return;
    }

    const supabase = getSupabaseClient();
    if (!supabase) {
      console.warn('[TaskManager] No Supabase client, skipping subscription');
      return;
    }

    console.debug(`[TaskManager] Initializing for user ${currentUserId.slice(0, 8)}...`);

    refreshTasks();

    const channel = supabase
      .channel(`user-tasks-${currentUserId}`)
      .on('postgres_changes', {
        event: 'INSERT',
        schema: 'public',
        table: 'task_tracking',
        filter: `user_id=eq.${currentUserId}`,
      }, (payload) => {
        dispatch({ type: 'INSERT', task: rowToTask(payload.new) });
      })
      .on('postgres_changes', {
        event: 'UPDATE',
        schema: 'public',
        table: 'task_tracking',
        filter: `user_id=eq.${currentUserId}`,
      }, (payload) => {
        dispatch({ type: 'UPDATE', task: rowToTask(payload.new) });
      })
      .on('postgres_changes', {
        event: 'DELETE',
        schema: 'public',
        table: 'task_tracking',
        filter: `user_id=eq.${currentUserId}`,
      }, (payload) => {
        // REPLICA IDENTITY FULL set in migration 180, so payload.old has the row.
        const oldRow = payload.old as { dbos_workflow_id?: string; id?: string };
        const id = oldRow.dbos_workflow_id || oldRow.id;
        if (id) dispatch({ type: 'DELETE', id });
      })
      .subscribe((status, err) => {
        console.debug(`[TaskManager] Realtime status: ${status}`, err || '');
        dispatch({ type: 'SET_CONNECTED', connected: status === 'SUBSCRIBED' });
        if (status === 'SUBSCRIBED') {
          refreshTasks();
        }
      });

    channelRef.current = channel;

    return () => {
      if (channelRef.current) {
        supabase.removeChannel(channelRef.current);
        channelRef.current = null;
      }
    };
  }, [currentUserId, refreshTasks]);

  // ─── Redis WebSocket for real-time progress ────────────
  const wsRef = useRef<WebSocket | null>(null);
  const wsReconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wsReconnectDelay = useRef(1000);

  useEffect(() => {
    if (!currentUserId) return;

    let unmounted = false;

    const connect = async () => {
      // Auth: short-lived one-shot ticket only. Putting a long-lived JWT
      // in the URL is a real leak risk — it lands in nginx / supabase /
      // any-proxy access logs, browser history, and the Referer header,
      // and it's a steal-and-replay credential while it lives. We used
      // to fall back to ?token=<JWT> when ticket acquisition failed
      // (typical during a watchexec reload), but that meant a single
      // backend hiccup converted every subsequent WS into a leaked
      // long-lived JWT. Better: don't connect at all until the ticket
      // endpoint is back; the reconnect-with-backoff loop already
      // handles retry cleanly.
      const session = await getSupabaseAccessToken();
      if (!session || unmounted) return;

      let ticket: string;
      try {
        const { wsTicketService } = await import('../services/wsTicketService');
        const r = await wsTicketService.acquire();
        ticket = r.ticket;
      } catch (err) {
        console.debug('[TaskManager/WS] ticket acquisition failed; will retry via onclose', err);
        if (unmounted) return;
        wsReconnectTimer.current = setTimeout(() => {
          if (!unmounted) connect();
        }, wsReconnectDelay.current);
        wsReconnectDelay.current = Math.min(wsReconnectDelay.current * 2, 30000);
        return;
      }
      if (unmounted) return;
      const wsUrl = `${getWsBaseUrl()}/ws/task-progress?ticket=${encodeURIComponent(ticket)}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.debug('[TaskManager/WS] Connected');
        wsReconnectDelay.current = 1000; // reset backoff
        dispatch({ type: 'SET_WS_CONNECTED', connected: true });
      };

      ws.onmessage = (event) => {
        try {
          const raw = JSON.parse(event.data);
          if (raw.type === 'download_started') {
            dispatch({ type: 'DOWNLOAD_STARTED', payload: raw as WsDownloadStartedPayload });
          } else {
            dispatch({ type: 'UPDATE_PROGRESS', payload: raw as WsProgressPayload });
          }
        } catch (e) {
          console.warn('[TaskManager/WS] Bad message:', e);
        }
      };

      ws.onclose = () => {
        dispatch({ type: 'SET_WS_CONNECTED', connected: false });
        if (unmounted) return;
        console.debug(`[TaskManager/WS] Disconnected, reconnecting in ${wsReconnectDelay.current}ms`);
        wsReconnectTimer.current = setTimeout(() => {
          if (!unmounted) connect();
        }, wsReconnectDelay.current);
        wsReconnectDelay.current = Math.min(wsReconnectDelay.current * 2, 30000);
      };

      ws.onerror = (err) => {
        console.debug('[TaskManager/WS] Error:', err);
        ws.close();
      };
    };

    connect();

    return () => {
      unmounted = true;
      if (wsReconnectTimer.current) clearTimeout(wsReconnectTimer.current);
      if (wsRef.current) {
        wsRef.current.onclose = null; // prevent reconnect on intentional close
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [currentUserId]);

  // ─── Resume on tab focus ────────────────────────────────
  // Realtime + WS can silently drop while the tab is hidden (mobile suspend,
  // OS sleep, network reshuffle). On return to visibility, re-pull the truth
  // from the DB so the UI doesn't show stale "Initializing… 30%" rows that
  // actually finished an hour ago.
  useEffect(() => {
    if (!currentUserId) return;
    const handler = () => {
      if (document.visibilityState === 'visible') {
        refreshTasks();
      }
    };
    document.addEventListener('visibilitychange', handler);
    return () => document.removeEventListener('visibilitychange', handler);
  }, [currentUserId, refreshTasks]);

  // ─── Polling backstop ───────────────────────────────────
  // When Realtime AND WS are BOTH down (both connections lost simultaneously),
  // neither push channel will tell us about state changes. Poll every 30s
  // until at least one channel comes back. Stops polling once any channel
  // reconnects so we don't double-pull when healthy.
  const bothChannelsDown = !state.isConnected && !state.isWsConnected;
  useEffect(() => {
    if (!currentUserId || !bothChannelsDown) return;
    const timer = setInterval(() => {
      // Skip when tab is hidden — visibilitychange handler covers resume.
      if (document.visibilityState === 'visible') {
        refreshTasks();
      }
    }, 30000);
    return () => clearInterval(timer);
  }, [currentUserId, bothChannelsDown, refreshTasks]);

  // Derived state
  const activeTasks = state.tasks.filter(
    t => t.status === 'pending' || t.status === 'processing'
  );
  const totalActive = activeTasks.length;
  const activeCounts: Record<TaskType, number> = {
    parse: 0,
    download: 0,
    upload: 0,
    transcode: 0,
    ai_pipeline: 0,
    ai_extract: 0,
    ai_transcription: 0,
    ai_summary: 0,
    agent: 0,
  };
  for (const t of activeTasks) {
    if (t.task_type in activeCounts) {
      activeCounts[t.task_type]++;
    }
  }

  // Actions
  const cancelTask = useCallback(async (taskId: string) => {
    await apiCancelTask(taskId);
  }, []);

  const retryTask = useCallback(async (taskId: string) => {
    await apiRetryTask(taskId);
  }, []);

  const deleteTask = useCallback(async (taskId: string) => {
    dispatch({ type: 'DELETE', id: taskId });
    await apiDeleteTask(taskId);
  }, []);

  const clearCompleted = useCallback(async () => {
    // Optimistic: remove completed/failed/cancelled from local state
    const completedIds = state.tasks
      .filter(t => ['completed', 'failed', 'cancelled'].includes(t.status))
      .map(t => t.id);
    for (const id of completedIds) {
      dispatch({ type: 'DELETE', id });
    }
    await apiClearCompleted();
  }, [state.tasks]);

  return (
    <TaskManagerContext.Provider value={{
      ...state,
      activeTasks,
      totalActive,
      activeCounts,
      cancelTask,
      retryTask,
      deleteTask,
      clearCompleted,
      refreshTasks,
    }}>
      {children}
    </TaskManagerContext.Provider>
  );
};

// ─── Hook ───────────────────────────────────────────────

export function useTaskManager(): TaskManagerContextType {
  const ctx = useContext(TaskManagerContext);
  if (!ctx) throw new Error('useTaskManager must be used within TaskManagerProvider');
  return ctx;
}

// ─── Utilities ──────────────────────────────────────────

export function formatSpeed(bytesPerSec: number): string {
  if (!bytesPerSec || bytesPerSec <= 0) return '';
  if (bytesPerSec < 1024) return `${bytesPerSec} B/s`;
  if (bytesPerSec < 1048576) return `${(bytesPerSec / 1024).toFixed(1)} KB/s`;
  return `${(bytesPerSec / 1048576).toFixed(1)} MB/s`;
}

export function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(2)} MB`;
  return `${(bytes / 1073741824).toFixed(2)} GB`;
}

export function taskTypeIcon(type: TaskType): string {
  switch (type) {
    case 'parse': return '\uD83D\uDD0D';         // 🔍
    case 'upload': return '\u2191';              // ↑
    case 'download': return '\u2193';            // ↓
    case 'transcode': return '\u27F3';           // ⟳
    case 'ai_pipeline': return '\u2726';         // ✦
    case 'ai_extract': return '\uD83C\uDFA4';    // 🎤 (audio extract)
    case 'ai_transcription': return '\uD83D\uDCDD'; // 📝 (transcription)
    case 'ai_summary': return '\u2726';           // ✦ (summary)
    case 'agent': return '\ud83e\udd16';     // \ud83e\udd16 (agent run)
    default: return '\u2022';
  }
}

export function taskTypeLabel(type: TaskType): string {
  switch (type) {
    case 'parse': return 'Parse';
    case 'upload': return 'Upload';
    case 'download': return 'Download';
    case 'transcode': return 'Transcode';
    case 'ai_pipeline': return 'AI Pipeline';
    case 'ai_extract': return 'Audio Extract';
    case 'ai_transcription': return 'Transcription';
    case 'ai_summary': return 'Summary';
    case 'agent': return 'Agent';
    default: return type;
  }
}

export function taskCategoryLabel(category: TaskCategory): string {
  switch (category) {
    case 'transfer': return 'Transfer';
    case 'processing': return 'Processing';
    case 'ai': return 'AI';
    default: return category;
  }
}

/** Get tasks grouped by their group_id (for AI pipeline sub-tasks) */
export function getTaskGroups(tasks: UnifiedTask[]): Map<string, UnifiedTask[]> {
  const groups = new Map<string, UnifiedTask[]>();
  for (const task of tasks) {
    if (task.group_id) {
      const existing = groups.get(task.group_id) || [];
      existing.push(task);
      groups.set(task.group_id, existing);
    }
  }
  return groups;
}

export function taskPhaseLabel(phase?: TaskPhase): string {
  switch (phase) {
    case 'queued': return 'Queued';
    case 'dedup_check': return 'Checking...';
    case 'processing': return 'Processing';
    case 'completed': return 'Done';
    case 'failed': return 'Failed';
    case 'cancelled': return 'Cancelled';
    default: return '';
  }
}

export function isRetryable(errorCode?: string): boolean {
  if (!errorCode) return true;
  const nonRetryable = ['RESOURCE_404', 'STORAGE_FULL', 'TRANSCODE_FAILED', 'AI_QUOTA_EXCEEDED'];
  return !nonRetryable.includes(errorCode);
}

export function errorCodeMessage(errorCode?: string): string {
  const messages: Record<string, string> = {
    'NETWORK_TIMEOUT': 'Network timeout, will auto-retry',
    'RESOURCE_404': 'Source deleted or unavailable',
    'STORAGE_FULL': 'Storage full, please free space',
    'RATE_LIMITED': 'Rate limited, retrying...',
    'TRANSCODE_FAILED': 'Transcode failed: unsupported format',
    'AI_QUOTA_EXCEEDED': 'AI quota exceeded',
    'UNKNOWN': 'Unexpected error',
  };
  return messages[errorCode || ''] || errorCode || 'Unknown error';
}
