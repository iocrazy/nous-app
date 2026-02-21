import React, { createContext, useContext, useReducer, useEffect, useCallback, useRef } from 'react';
import { getSupabaseClient } from '../supabaseClient';
import { useAuth } from './AuthContext';
import { getAuthHeaders } from '../services/parserService';

// ─── Types ──────────────────────────────────────────────

export type TaskType =
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

type Action =
  | { type: 'SET_TASKS'; tasks: UnifiedTask[] }
  | { type: 'INSERT'; task: UnifiedTask }
  | { type: 'UPDATE'; task: UnifiedTask }
  | { type: 'DELETE'; id: string }
  | { type: 'SET_LOADING'; loading: boolean }
  | { type: 'SET_CONNECTED'; connected: boolean };

function reducer(state: TaskManagerState, action: Action): TaskManagerState {
  switch (action.type) {
    case 'SET_TASKS':
      return { ...state, tasks: action.tasks, isLoading: false };
    case 'INSERT':
      // Avoid duplicates
      if (state.tasks.some(t => t.id === action.task.id)) {
        return {
          ...state,
          tasks: state.tasks.map(t => t.id === action.task.id ? action.task : t),
        };
      }
      return { ...state, tasks: [action.task, ...state.tasks] };
    case 'UPDATE':
      return {
        ...state,
        tasks: state.tasks.map(t => t.id === action.task.id ? action.task : t),
      };
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

async function fetchActiveTasks(): Promise<UnifiedTask[]> {
  const resp = await fetch(`${API_BASE}/api/v1/task-manager/tasks/active`, {
    headers: await getAuthHeaders(),
  });
  if (!resp.ok) return [];
  const json = await resp.json();
  return json.data || [];
}

async function fetchAllTasks(limit = 200): Promise<UnifiedTask[]> {
  const resp = await fetch(`${API_BASE}/api/v1/task-manager/tasks?limit=${limit}`, {
    headers: await getAuthHeaders(),
  });
  if (!resp.ok) return [];
  const json = await resp.json();
  return json.data || [];
}

async function apiCancelTask(taskId: string): Promise<void> {
  await fetch(`${API_BASE}/api/v1/task-manager/tasks/${taskId}/cancel`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
}

async function apiRetryTask(taskId: string): Promise<void> {
  await fetch(`${API_BASE}/api/v1/task-manager/tasks/${taskId}/retry`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
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

  // Supabase Realtime subscription
  useEffect(() => {
    if (!currentUserId) return;

    const supabase = getSupabaseClient();
    if (!supabase) return;

    // Fetch initial data
    refreshTasks();

    // Subscribe to realtime changes
    const channel = supabase
      .channel(`user-tasks-${currentUserId}`)
      .on('postgres_changes', {
        event: 'INSERT',
        schema: 'public',
        table: 'unified_tasks',
        filter: `user_id=eq.${currentUserId}`,
      }, (payload) => {
        dispatch({ type: 'INSERT', task: payload.new as UnifiedTask });
      })
      .on('postgres_changes', {
        event: 'UPDATE',
        schema: 'public',
        table: 'unified_tasks',
        filter: `user_id=eq.${currentUserId}`,
      }, (payload) => {
        dispatch({ type: 'UPDATE', task: payload.new as UnifiedTask });
      })
      .on('postgres_changes', {
        event: 'DELETE',
        schema: 'public',
        table: 'unified_tasks',
        filter: `user_id=eq.${currentUserId}`,
      }, (payload) => {
        dispatch({ type: 'DELETE', id: (payload.old as { id: string }).id });
      })
      .subscribe((status) => {
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

  // Derived state
  const activeTasks = state.tasks.filter(
    t => t.status === 'pending' || t.status === 'processing'
  );
  const totalActive = activeTasks.length;
  const activeCounts: Record<TaskType, number> = {
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
