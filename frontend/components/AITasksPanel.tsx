import React, { useState, useEffect, useCallback } from 'react';
import {
  Clock,
  CheckCircle,
  XCircle,
  RefreshCw,
  Loader2,
  Filter,
  RotateCcw,
  Brain,
  FileText,
  PenTool,
  Eye,
} from 'lucide-react';
import { getSupabaseClient } from '../supabaseClient';
import { triggerTranscription, triggerSummary, triggerVisualAnalysis } from '../services/aiService';
import { formatRelativeTime } from '../services/taskService';
import { useToast } from './Toast';

type AIStatus = 'pending' | 'processing' | 'completed' | 'failed';
type TaskTypeFilter = 'all' | 'transcription' | 'summary' | 'visual_analysis';
type StatusFilter = '' | AIStatus;

interface AITaskVideo {
  id: string;
  platform_id: string;
  title: string;
  transcript_status: AIStatus | null;
  summary_status: AIStatus | null;
  visual_analysis_status: AIStatus | null;
  updated_at: string;
}

interface AITaskStats {
  processing: number;
  completed: number;
  failed: number;
  pending: number;
}

// Map task types to UI labels
const TASK_LABELS: Record<string, string> = {
  transcription: 'Extract',
  summary: 'Rewrite',
  visual_analysis: 'Analyze',
};

const getOverallStatus = (video: AITaskVideo): AIStatus => {
  const statuses = [
    video.transcript_status,
    video.summary_status,
    video.visual_analysis_status,
  ].filter(Boolean) as AIStatus[];

  if (statuses.includes('processing')) return 'processing';
  if (statuses.includes('failed')) return 'failed';
  if (statuses.includes('pending')) return 'pending';
  return 'completed';
};

const getStatusIcon = (status: AIStatus | null) => {
  switch (status) {
    case 'pending':
      return <Clock size={14} className="text-yellow-500" />;
    case 'processing':
      return <Loader2 size={14} className="text-blue-500 animate-spin" />;
    case 'completed':
      return <CheckCircle size={14} className="text-green-500" />;
    case 'failed':
      return <XCircle size={14} className="text-red-500" />;
    default:
      return <span className="text-zinc-600">-</span>;
  }
};

const getStatusBadge = (status: AIStatus | null) => {
  if (!status) return <span className="text-xs text-zinc-600">-</span>;

  const styles: Record<AIStatus, string> = {
    pending: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20',
    processing: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
    completed: 'bg-green-500/10 text-green-400 border-green-500/20',
    failed: 'bg-red-500/10 text-red-400 border-red-500/20',
  };

  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full border ${styles[status]}`}>
      {getStatusIcon(status)}
      {status}
    </span>
  );
};

export const AITasksPanel: React.FC = () => {
  const [videos, setVideos] = useState<AITaskVideo[]>([]);
  const [stats, setStats] = useState<AITaskStats>({ processing: 0, completed: 0, failed: 0, pending: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState<TaskTypeFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [retryingIds, setRetryingIds] = useState<Set<string>>(new Set());
  const [retryErrors, setRetryErrors] = useState<Record<string, string>>({});
  const { addToast } = useToast();

  const fetchData = useCallback(async () => {
    const supabase = getSupabaseClient();
    if (!supabase) {
      setError('Supabase not configured');
      setLoading(false);
      return;
    }

    try {
      setIsRefreshing(true);

      // Build query for videos with any AI task activity
      let query = supabase
        .from('videos')
        .select('id, platform_id, title, transcript_status, summary_status, visual_analysis_status, updated_at')
        .or('transcript_status.neq.pending,summary_status.neq.pending,visual_analysis_status.neq.pending')
        .order('updated_at', { ascending: false })
        .limit(50);

      // Apply type filter
      if (typeFilter === 'transcription') {
        query = query.neq('transcript_status', 'pending');
      } else if (typeFilter === 'summary') {
        query = query.neq('summary_status', 'pending');
      } else if (typeFilter === 'visual_analysis') {
        query = query.neq('visual_analysis_status', 'pending');
      }

      const { data, error: fetchError } = await query;

      if (fetchError) throw fetchError;

      let filtered = (data || []) as AITaskVideo[];

      // Apply status filter client-side
      if (statusFilter) {
        filtered = filtered.filter((v) => {
          if (typeFilter === 'transcription') return v.transcript_status === statusFilter;
          if (typeFilter === 'summary') return v.summary_status === statusFilter;
          if (typeFilter === 'visual_analysis') return v.visual_analysis_status === statusFilter;
          return getOverallStatus(v) === statusFilter;
        });
      }

      setVideos(filtered);

      // Compute stats from all data (unfiltered)
      const allVideos = (data || []) as AITaskVideo[];
      const newStats: AITaskStats = { processing: 0, completed: 0, failed: 0, pending: 0 };
      allVideos.forEach((v) => {
        const statuses = [v.transcript_status, v.summary_status, v.visual_analysis_status];
        if (statuses.includes('processing')) newStats.processing++;
        if (statuses.includes('failed')) newStats.failed++;
        if (statuses.includes('completed')) newStats.completed++;
        if (statuses.includes('pending') && statuses.some((s) => s && s !== 'pending')) newStats.pending++;
      });
      setStats(newStats);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch AI tasks');
    } finally {
      setLoading(false);
      setIsRefreshing(false);
    }
  }, [typeFilter, statusFilter]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleRetry = async (video: AITaskVideo, taskType: 'transcription' | 'summary' | 'visual_analysis') => {
    const key = `${video.platform_id}-${taskType}`;
    setRetryingIds((prev) => new Set(prev).add(key));
    // Clear previous error for this key
    setRetryErrors((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });

    try {
      if (taskType === 'transcription') {
        await triggerTranscription(video.platform_id);
      } else if (taskType === 'summary') {
        await triggerSummary(video.platform_id);
      } else {
        await triggerVisualAnalysis(video.platform_id);
      }
      addToast(`${TASK_LABELS[taskType]} task restarted`, 'success');
      setTimeout(fetchData, 2000);
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Unknown error';
      setRetryErrors((prev) => ({ ...prev, [key]: errorMsg }));
      addToast(`${TASK_LABELS[taskType]} failed: ${errorMsg}`, 'error');
    } finally {
      setRetryingIds((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  };

  const renderRetryButton = (
    video: AITaskVideo,
    taskType: 'transcription' | 'summary' | 'visual_analysis',
    icon: React.ReactNode
  ) => {
    const key = `${video.platform_id}-${taskType}`;
    const isRetrying = retryingIds.has(key);

    return (
      <button
        onClick={() => handleRetry(video, taskType)}
        disabled={isRetrying}
        className="inline-flex items-center gap-1 px-2 py-1 bg-orange-500/10 text-orange-400 rounded text-xs hover:bg-orange-500/20 transition-colors disabled:opacity-50"
        title={`Retry ${TASK_LABELS[taskType]}`}
      >
        {isRetrying ? (
          <Loader2 size={12} className="animate-spin" />
        ) : (
          <RotateCcw size={12} />
        )}
        {icon}
        {TASK_LABELS[taskType]}
      </button>
    );
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <RefreshCw size={24} className="animate-spin text-indigo-500" />
      </div>
    );
  }

  // Collect all failed videos with retry errors
  const failedWithErrors = videos.filter((v) => {
    const keys = [
      `${v.platform_id}-transcription`,
      `${v.platform_id}-summary`,
      `${v.platform_id}-visual_analysis`,
    ];
    return keys.some((k) => retryErrors[k]);
  });

  return (
    <div className="space-y-3 md:space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-purple-500/10 rounded-lg">
            <Brain size={20} className="text-purple-400" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-white">AI Tasks</h2>
            <p className="text-sm text-zinc-400">Monitor AI processing status</p>
          </div>
        </div>
        <button
          onClick={() => fetchData()}
          disabled={isRefreshing}
          className="p-2 hover:bg-zinc-800 rounded-lg transition-colors"
        >
          <RefreshCw size={18} className={`text-zinc-400 ${isRefreshing ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {error && (
        <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Stats Cards - compact on mobile */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 md:gap-4">
        <div className="p-2.5 md:p-4 bg-zinc-900 rounded-lg border border-zinc-800">
          <div className="flex items-center gap-2 mb-1 md:mb-2">
            <Loader2 size={14} className="text-blue-500" />
            <span className="text-xs md:text-sm text-zinc-400">Processing</span>
          </div>
          <div className="text-xl md:text-2xl font-bold text-white">{stats.processing}</div>
        </div>
        <div className="p-2.5 md:p-4 bg-zinc-900 rounded-lg border border-zinc-800">
          <div className="flex items-center gap-2 mb-1 md:mb-2">
            <CheckCircle size={14} className="text-green-500" />
            <span className="text-xs md:text-sm text-zinc-400">Completed</span>
          </div>
          <div className="text-xl md:text-2xl font-bold text-white">{stats.completed}</div>
        </div>
        <div className="p-2.5 md:p-4 bg-zinc-900 rounded-lg border border-zinc-800">
          <div className="flex items-center gap-2 mb-1 md:mb-2">
            <XCircle size={14} className="text-red-500" />
            <span className="text-xs md:text-sm text-zinc-400">Failed</span>
          </div>
          <div className="text-xl md:text-2xl font-bold text-white">{stats.failed}</div>
        </div>
        <div className="p-2.5 md:p-4 bg-zinc-900 rounded-lg border border-zinc-800">
          <div className="flex items-center gap-2 mb-1 md:mb-2">
            <Clock size={14} className="text-yellow-500" />
            <span className="text-xs md:text-sm text-zinc-400">Pending</span>
          </div>
          <div className="text-xl md:text-2xl font-bold text-white">{stats.pending}</div>
        </div>
      </div>

      {/* Filters - compact */}
      <div className="flex flex-wrap items-center gap-2 md:gap-4">
        <div className="flex items-center gap-2">
          <Filter size={16} className="text-zinc-400" />
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value as TaskTypeFilter)}
            className="px-3 py-1.5 md:py-2 bg-zinc-900 border border-zinc-800 rounded-lg text-sm text-white focus:outline-none focus:border-indigo-500"
          >
            <option value="all">All Types</option>
            <option value="transcription">Extract</option>
            <option value="summary">Rewrite</option>
            <option value="visual_analysis">Analyze</option>
          </select>
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
          className="px-3 py-1.5 md:py-2 bg-zinc-900 border border-zinc-800 rounded-lg text-sm text-white focus:outline-none focus:border-indigo-500"
        >
          <option value="">All Status</option>
          <option value="pending">Pending</option>
          <option value="processing">Processing</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
        </select>
      </div>

      {/* Task Table - scrollable on mobile */}
      <div className="bg-zinc-900 rounded-lg border border-zinc-800 overflow-hidden max-h-[40vh] md:max-h-none overflow-y-auto">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-zinc-800 text-left">
                <th className="px-4 py-3 text-sm font-medium text-zinc-400">Status</th>
                <th className="px-4 py-3 text-sm font-medium text-zinc-400">Video</th>
                <th className="px-4 py-3 text-sm font-medium text-zinc-400">
                  <div className="flex items-center gap-1">
                    <FileText size={14} />
                    Extract
                  </div>
                </th>
                <th className="px-4 py-3 text-sm font-medium text-zinc-400">
                  <div className="flex items-center gap-1">
                    <PenTool size={14} />
                    Rewrite
                  </div>
                </th>
                <th className="px-4 py-3 text-sm font-medium text-zinc-400">
                  <div className="flex items-center gap-1">
                    <Eye size={14} />
                    Analyze
                  </div>
                </th>
                <th className="px-4 py-3 text-sm font-medium text-zinc-400">Updated</th>
                <th className="px-4 py-3 text-sm font-medium text-zinc-400">Actions</th>
              </tr>
            </thead>
            <tbody>
              {videos.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-zinc-500">
                    No AI tasks found
                  </td>
                </tr>
              ) : (
                videos.map((video) => {
                  const overall = getOverallStatus(video);

                  return (
                    <tr key={video.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          {getStatusIcon(overall)}
                          <span className={`text-sm capitalize ${
                            overall === 'processing' ? 'text-blue-400' :
                            overall === 'failed' ? 'text-red-400' :
                            overall === 'pending' ? 'text-yellow-400' :
                            'text-green-400'
                          }`}>
                            {overall}
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="max-w-[200px] truncate text-sm text-white" title={video.title}>
                          {video.title || 'Untitled'}
                        </div>
                        <div className="text-xs text-zinc-500">{video.platform_id}</div>
                      </td>
                      <td className="px-4 py-3">{getStatusBadge(video.transcript_status)}</td>
                      <td className="px-4 py-3">{getStatusBadge(video.summary_status)}</td>
                      <td className="px-4 py-3">{getStatusBadge(video.visual_analysis_status)}</td>
                      <td className="px-4 py-3 text-sm text-zinc-500">
                        {formatRelativeTime(video.updated_at)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap items-center gap-1">
                          {video.transcript_status === 'failed' &&
                            renderRetryButton(video, 'transcription', <FileText size={12} />)}
                          {video.summary_status === 'failed' &&
                            renderRetryButton(video, 'summary', <PenTool size={12} />)}
                          {video.visual_analysis_status === 'failed' &&
                            renderRetryButton(video, 'visual_analysis', <Eye size={12} />)}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Error Details */}
      {failedWithErrors.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-zinc-400">Retry Error Details</h3>
          {failedWithErrors.map((video) => {
            const errors = (['transcription', 'summary', 'visual_analysis'] as const)
              .map((t) => ({ type: t, error: retryErrors[`${video.platform_id}-${t}`] }))
              .filter((e) => e.error);

            return (
              <div key={video.id} className="p-3 bg-red-500/5 border border-red-500/20 rounded-lg">
                <div className="text-sm text-white truncate">{video.title || video.platform_id}</div>
                {errors.map((e) => (
                  <div key={e.type} className="text-xs text-red-400 mt-1">
                    {TASK_LABELS[e.type]}: {e.error}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default AITasksPanel;
