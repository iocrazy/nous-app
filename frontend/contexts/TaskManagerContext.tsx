import React, { createContext, useContext, useReducer, useEffect, useCallback, useRef } from 'react';
import { getSupabaseClient, getSupabaseAccessToken } from '../supabaseClient';
import { useAuth } from './AuthContext';
import { getAuthHeaders } from '../services/parserService';
import {
  cancelWorkflow,
  listWorkflows,
  restartWorkflow,
} from '../services/dbosWorkflowService';
import {
  dbosRowToUnifiedTask,
  dbosSnapshotToUnifiedTask,
  type DbosWorkflowStatusRow,
} from './dbosTaskMapper';

// ─── Types ──────────────────────────────────────────────

export type TaskType =
  | 'parse'
  | 'download'
  | 'upload'
  | 'transcode'
  | 'ai_pipeline'
  | 'ai_extract'
  | 'ai_transcription'
  | 'ai_summary';

export type TaskCategory = 'transfer' | 'processing' | 'ai';

export type TaskStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled';

export type TaskPhase = 'queued' | 'dedup_check' | 'processing' | 'completed' | 'failed' | 'cancelled';

export interface UnifiedTask {
  id: string;
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
  celery_task_id?: string;
  metadata: Record<string, unknown>;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  updated_at?: string;
  phase?: TaskPhase;
  dedup_key?: string;
  subscribers?: Array<{ user_id: string; resource_id: string; subscribed_at: string }>;
  error_code?: string;
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
  isConnected: boolean;
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
  | { type: 'SET_CONNECTED'; connected: boolean };

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

async function fetchAllTasks(limit = 200, fallbackUserId?: string): Promise<UnifiedTask[]> {
  try {
    const resp = await listWorkflows({ limit, sortDesc: true });
    const mapped = resp.workflows
      .map(w => dbosSnapshotToUnifiedTask(w, { fallbackUserId }))
      .filter((t): t is UnifiedTask => t !== null);
    console.debug(`[TaskManager] listWorkflows returned ${mapped.length} workflows`);
    return mapped;
  } catch (e) {
    console.error('[TaskManager] listWorkflows failed:', e);
    return [];
  }
}

async function apiCancelTask(taskId: string): Promise<void> {
  // taskId IS the DBOS workflow_id (since D8-1).
  try {
    await cancelWorkflow(taskId);
  } catch (e) {
    console.error(`[TaskManager] cancelWorkflow(${taskId}) failed:`, e);
  }
}

async function apiRetryTask(taskId: string): Promise<void> {
  // restartWorkflow forks a NEW workflow_id; the original row stays in
  // a terminal state (clearer audit than mutating in place). The new
  // workflow's INSERT will land via Realtime.
  try {
    await restartWorkflow(taskId);
  } catch (e) {
    console.error(`[TaskManager] restartWorkflow(${taskId}) failed:`, e);
  }
}

async function apiDeleteTask(_taskId: string): Promise<void> {
  // DBOS doesn't expose a hard-delete; D8-3 will add a soft-hide
  // endpoint or just rely on retention. Local-only for now.
}

async function apiClearCompleted(): Promise<void> {
  // Local-only — see apiDeleteTask.
}

// ─── Context ────────────────────────────────────────────

const TaskManagerContext = createContext<TaskManagerContextType | null>(null);

export const TaskManagerProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { currentUserId } = useAuth();
  const [state, dispatch] = useReducer(reducer, {
    tasks: [],
    isLoading: true,
    isConnected: false,
  });
  const channelRef = useRef<ReturnType<ReturnType<typeof getSupabaseClient>['channel']> | null>(null);

  // Initial fetch
  const refreshTasks = useCallback(async () => {
    dispatch({ type: 'SET_LOADING', loading: true });
    try {
      const tasks = await fetchAllTasks(200, currentUserId || undefined);
      dispatch({ type: 'SET_TASKS', tasks });
    } catch (e) {
      console.error('[TaskManager] Failed to fetch tasks:', e);
      dispatch({ type: 'SET_LOADING', loading: false });
    }
  }, [currentUserId]);

  // Supabase Realtime subscription — D8-1 single source = dbos.workflow_status.
  // RLS gates rows to the current user (authenticated_user = auth.uid()),
  // so no client-side filter is needed. INSERT = workflow dispatched,
  // UPDATE = lifecycle transition (RUNNING/SUCCESS/ERROR/CANCELLED).
  // DBOS rows are never deleted — clearCompleted only hides locally.
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

    // Fetch initial data
    refreshTasks();

    const channel = supabase
      .channel(`user-dbos-workflows-${currentUserId}`)
      .on('postgres_changes', {
        event: 'INSERT',
        schema: 'dbos',
        table: 'workflow_status',
      }, (payload) => {
        const task = dbosRowToUnifiedTask(
          payload.new as DbosWorkflowStatusRow,
          { fallbackUserId: currentUserId }
        );
        if (task) dispatch({ type: 'INSERT', task });
      })
      .on('postgres_changes', {
        event: 'UPDATE',
        schema: 'dbos',
        table: 'workflow_status',
      }, (payload) => {
        const task = dbosRowToUnifiedTask(
          payload.new as DbosWorkflowStatusRow,
          { fallbackUserId: currentUserId }
        );
        if (task) dispatch({ type: 'UPDATE', task });
      })
      .subscribe((status, err) => {
        console.debug(`[TaskManager] Realtime status: ${status}`, err || '');
        dispatch({ type: 'SET_CONNECTED', connected: status === 'SUBSCRIBED' });
        if (status === 'SUBSCRIBED') {
          // Re-fetch on reconnect to fill any gaps
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
      const token = await getSupabaseAccessToken();
      if (!token || unmounted) return;

      const wsUrl = `${getWsBaseUrl()}/ws/task-progress?token=${encodeURIComponent(token)}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.debug('[TaskManager/WS] Connected');
        wsReconnectDelay.current = 1000; // reset backoff
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
