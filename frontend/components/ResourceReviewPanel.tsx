import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Send,
  Loader2,
  MessageSquare,
  Clock,
  X,
  CheckCircle2,
  XCircle,
  RotateCcw,
  Trash2,
  ChevronDown,
  PenTool,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  ReviewComment,
  ReviewAnnotation,
  fetchComments,
  createComment,
  resolveComment,
  reopenComment,
  deleteComment,
  setReviewStatus,
  fetchReviewStatuses,
  ReviewStatus,
} from '../services/reviewService';
import { ReviewStatusBadge } from './ReviewStatusBadge';

// ─── Types ──────────────────────────────────────────────

interface ResourceReviewPanelProps {
  resourceId: string;
  versionId?: string;
  currentUserId: string;
  currentTime: number;
  isVideo: boolean;
  onSeekTo: (seconds: number) => void;
  onStartAnnotation: () => void;
  pendingAnnotations?: Array<{ tool_type: string; data: Record<string, unknown> }>;
  onClearAnnotations?: () => void;
  onViewAnnotations?: (annotations: ReviewAnnotation[]) => void;
  onCommentChange?: () => void;
}

// ─── Helpers ────────────────────────────────────────────

function formatTimecode(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

// ─── CommentItem ────────────────────────────────────────

const CommentItem: React.FC<{
  comment: ReviewComment;
  currentUserId: string;
  onSeekTo: (s: number) => void;
  onResolve: (id: string) => void;
  onReopen: (id: string) => void;
  onDelete: (id: string) => void;
  onViewAnnotations?: (annotations: ReviewAnnotation[]) => void;
}> = ({ comment, currentUserId, onSeekTo, onResolve, onReopen, onDelete, onViewAnnotations }) => {
  const isResolved = comment.status === 'resolved';
  const isAuthor = comment.author_id === currentUserId;
  const hasAnnotations = comment.annotations && comment.annotations.length > 0;

  return (
    <div className={`px-3 py-2.5 ${isResolved ? 'opacity-60' : ''}`}>
      {/* Header: timecode + status + actions */}
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-1.5">
          {comment.timecode != null && (
            <button
              onClick={() => onSeekTo(comment.timecode!)}
              className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-mono font-medium bg-indigo-500/15 text-indigo-400 rounded hover:bg-indigo-500/25 transition-colors"
            >
              <Clock size={10} />
              {formatTimecode(comment.timecode)}
            </button>
          )}
          <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
            isResolved
              ? 'bg-emerald-500/15 text-emerald-400'
              : 'bg-blue-500/15 text-blue-400'
          }`}>
            {isResolved ? 'Resolved' : 'Open'}
          </span>
          {hasAnnotations && (
            <button
              onClick={() => onViewAnnotations?.(comment.annotations!)}
              className="flex items-center gap-0.5 px-1 py-0.5 text-[10px] text-amber-400 bg-amber-500/10 rounded hover:bg-amber-500/20 transition-colors"
            >
              <PenTool size={9} />
              {comment.annotations!.length}
            </button>
          )}
        </div>
        <div className="flex items-center gap-0.5">
          {isResolved ? (
            <button
              onClick={() => onReopen(comment.id)}
              className="p-1 text-ink-500 hover:text-ink-300 transition-colors"
              title="Reopen"
            >
              <RotateCcw size={12} />
            </button>
          ) : (
            <button
              onClick={() => onResolve(comment.id)}
              className="p-1 text-ink-500 hover:text-emerald-400 transition-colors"
              title="Resolve"
            >
              <CheckCircle2 size={12} />
            </button>
          )}
          {isAuthor && (
            <button
              onClick={() => onDelete(comment.id)}
              className="p-1 text-ink-500 hover:text-red-400 transition-colors"
              title="Delete"
            >
              <Trash2 size={12} />
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      <p className="text-xs text-ink-300 leading-relaxed whitespace-pre-wrap">{comment.content}</p>

      {/* Footer */}
      <p className="text-[10px] text-ink-600 mt-1">{timeAgo(comment.created_at)}</p>

      {/* Replies */}
      {comment.replies && comment.replies.length > 0 && (
        <div className="ml-3 mt-2 pl-2 border-l border-ink-800 space-y-2">
          {comment.replies.map((reply) => (
            <div key={reply.id} className="text-xs">
              <p className="text-ink-400">{reply.content}</p>
              <p className="text-[10px] text-ink-600 mt-0.5">{timeAgo(reply.created_at)}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ─── StatusControls ─────────────────────────────────────

const StatusControls: React.FC<{
  resourceId: string;
  versionId?: string;
  statuses: ReviewStatus[];
  currentUserId: string;
  onStatusChange: () => void;
}> = ({ resourceId, versionId, statuses, currentUserId, onStatusChange }) => {
  const myStatus = statuses.find((s) => s.reviewer_id === currentUserId);
  const [showDropdown, setShowDropdown] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const handleSetStatus = async (status: string) => {
    try {
      await setReviewStatus({
        resource_id: resourceId,
        version_id: versionId,
        status,
      });
      setShowDropdown(false);
      onStatusChange();
    } catch (err) {
      console.error('Review status change failed:', err);
    }
  };

  return (
    <div className="px-3 py-2 border-b border-ink-800">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold text-ink-500 uppercase tracking-widest">
          Review Status
        </span>
        {myStatus && <ReviewStatusBadge status={myStatus.status} />}
      </div>
      <div className="flex items-center gap-1.5 mt-2" ref={dropdownRef}>
        <button
          onClick={() => handleSetStatus('approved')}
          className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium bg-emerald-500/15 text-emerald-400 rounded hover:bg-emerald-500/25 transition-colors"
        >
          <CheckCircle2 size={12} />
          Approve
        </button>
        <button
          onClick={() => handleSetStatus('rejected')}
          className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium bg-red-500/15 text-red-400 rounded hover:bg-red-500/25 transition-colors"
        >
          <XCircle size={12} />
          Reject
        </button>
        <div className="relative">
          <button
            onClick={() => setShowDropdown(!showDropdown)}
            className="flex items-center gap-0.5 px-2 py-1 text-[10px] font-medium bg-ink-800 text-ink-400 rounded hover:bg-ink-700 transition-colors"
          >
            More
            <ChevronDown size={10} />
          </button>
          {showDropdown && (
            <div className="absolute right-0 top-full mt-1 z-20 bg-ink-900 border border-ink-700 rounded-lg shadow-xl py-1 w-36">
              <button
                onClick={() => handleSetStatus('needs_changes')}
                className="w-full text-left px-3 py-1.5 text-[10px] text-amber-400 hover:bg-ink-800 transition-colors"
              >
                Needs Changes
              </button>
              <button
                onClick={() => handleSetStatus('pending')}
                className="w-full text-left px-3 py-1.5 text-[10px] text-ink-400 hover:bg-ink-800 transition-colors"
              >
                Reset to Pending
              </button>
            </div>
          )}
        </div>
      </div>
      {/* Other reviewers */}
      {statuses.length > 0 && (
        <div className="mt-2 space-y-1">
          {statuses.filter((s) => s.reviewer_id !== currentUserId).map((s) => (
            <div key={s.id} className="flex items-center justify-between">
              <span className="text-[10px] text-ink-500 truncate">Reviewer</span>
              <ReviewStatusBadge status={s.status} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ─── Main Panel ─────────────────────────────────────────

export const ResourceReviewPanel: React.FC<ResourceReviewPanelProps> = ({
  resourceId,
  versionId,
  currentUserId,
  currentTime,
  isVideo,
  onSeekTo,
  onStartAnnotation,
  pendingAnnotations,
  onClearAnnotations,
  onViewAnnotations,
  onCommentChange,
}) => {
  const { t } = useTranslation();
  const [comments, setComments] = useState<ReviewComment[]>([]);
  const [statuses, setStatuses] = useState<ReviewStatus[]>([]);
  const [content, setContent] = useState('');
  const [capturedTime, setCapturedTime] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [filterStatus, setFilterStatus] = useState<string>('all');

  const loadData = useCallback(async () => {
    setIsLoading(true);
    try {
      const statusFilter = filterStatus === 'all' ? undefined : filterStatus;
      const [cmts, sts] = await Promise.all([
        fetchComments(resourceId, versionId, statusFilter),
        fetchReviewStatuses(resourceId, versionId),
      ]);
      setComments(cmts);
      setStatuses(sts);
    } catch (err) {
      console.error('Failed to load review data:', err);
    } finally {
      setIsLoading(false);
    }
  }, [resourceId, versionId, filterStatus]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSubmit = async () => {
    const trimmed = content.trim();
    if (!trimmed || isSubmitting) return;

    setIsSubmitting(true);
    try {
      await createComment({
        resource_id: resourceId,
        version_id: versionId,
        content: trimmed,
        timecode: capturedTime ?? undefined,
        annotations: pendingAnnotations && pendingAnnotations.length > 0
          ? pendingAnnotations
          : undefined,
      });
      setContent('');
      setCapturedTime(null);
      onClearAnnotations?.();
      await loadData();
      onCommentChange?.();
    } catch (err) {
      console.error('Failed to create comment:', err);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleResolve = async (id: string) => {
    try {
      await resolveComment(id);
      await loadData();
    } catch { /* ignore */ }
  };

  const handleReopen = async (id: string) => {
    try {
      await reopenComment(id);
      await loadData();
    } catch { /* ignore */ }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteComment(id);
      await loadData();
      onCommentChange?.();
    } catch { /* ignore */ }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Review Status */}
      <StatusControls
        resourceId={resourceId}
        versionId={versionId}
        statuses={statuses}
        currentUserId={currentUserId}
        onStatusChange={loadData}
      />

      {/* Filter tabs */}
      <div className="flex items-center gap-1 px-3 py-1.5 border-b border-ink-800">
        {['all', 'open', 'resolved'].map((f) => (
          <button
            key={f}
            onClick={() => setFilterStatus(f)}
            className={`px-2 py-0.5 text-[10px] font-medium rounded transition-colors ${
              filterStatus === f
                ? 'bg-ink-700 text-ink-200'
                : 'text-ink-500 hover:text-ink-300'
            }`}
          >
            {f === 'all' ? `All (${comments.length})` : f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {/* Comments list */}
      <div className="flex-1 overflow-y-auto min-h-0 divide-y divide-ink-800/50">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 size={18} className="animate-spin text-ink-500" />
          </div>
        ) : comments.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-ink-500">
            <MessageSquare size={24} className="mb-2 opacity-40" />
            <span className="text-xs">{t('review.noComments', 'No comments yet')}</span>
          </div>
        ) : (
          comments.map((comment) => (
            <CommentItem
              key={comment.id}
              comment={comment}
              currentUserId={currentUserId}
              onSeekTo={onSeekTo}
              onResolve={handleResolve}
              onReopen={handleReopen}
              onDelete={handleDelete}
              onViewAnnotations={onViewAnnotations}
            />
          ))
        )}
      </div>

      {/* Add comment input */}
      <div className="p-3 border-t border-ink-800 shrink-0">
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t('review.commentPlaceholder', 'Add a comment... (Cmd+Enter to send)')}
          rows={2}
          className="w-full bg-ink-800/60 border border-ink-700/30 rounded-lg px-3 py-2 text-xs text-ink-200 placeholder-ink-600 resize-none focus:outline-none focus:border-indigo-500/50 transition-colors"
        />
        <div className="flex items-center justify-between mt-1.5">
          <div className="flex items-center gap-1.5">
            {isVideo && (
              <>
                <button
                  onClick={() => setCapturedTime(currentTime)}
                  className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] text-ink-400 hover:text-ink-200 hover:bg-ink-800 rounded transition-colors"
                >
                  <Clock size={11} />
                  Pin Time
                </button>
                <button
                  onClick={onStartAnnotation}
                  className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] text-ink-400 hover:text-ink-200 hover:bg-ink-800 rounded transition-colors"
                >
                  <PenTool size={11} />
                  Draw
                </button>
              </>
            )}
            {capturedTime != null && (
              <span className="flex items-center gap-0.5 px-1.5 py-0.5 text-[10px] font-mono bg-indigo-500/15 text-indigo-400 rounded">
                [{formatTimecode(capturedTime)}]
                <button onClick={() => setCapturedTime(null)} className="hover:text-indigo-200">
                  <X size={9} />
                </button>
              </span>
            )}
            {pendingAnnotations && pendingAnnotations.length > 0 && (
              <span className="flex items-center gap-0.5 px-1.5 py-0.5 text-[10px] bg-amber-500/15 text-amber-400 rounded">
                <PenTool size={9} />
                {pendingAnnotations.length}
                <button onClick={onClearAnnotations} className="hover:text-amber-200">
                  <X size={9} />
                </button>
              </span>
            )}
          </div>
          <button
            onClick={handleSubmit}
            disabled={!content.trim() || isSubmitting}
            className="flex items-center gap-1 px-2.5 py-1 text-[10px] font-medium bg-indigo-600 text-white rounded-lg hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {isSubmitting ? <Loader2 size={11} className="animate-spin" /> : <Send size={11} />}
            Send
          </button>
        </div>
      </div>
    </div>
  );
};
