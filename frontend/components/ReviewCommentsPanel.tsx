import React, { useState, useEffect, useCallback } from 'react';
import { Clock, Send, X, Loader2, MessageSquare } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ReviewComment } from '../types';
import { fetchComments, addComment, deleteComment } from '../services/projectsService';
import { ReviewCommentItem } from './ReviewCommentItem';

interface ReviewCommentsPanelProps {
  projectId: string;
  fileId: string;
  versionId?: string;
  currentTime: number;
  currentUserId: string;
  onSeekTo: (seconds: number) => void;
  onCommentAdded: () => void;
}

const formatTimestamp = (seconds: number): string => {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
};

export const ReviewCommentsPanel: React.FC<ReviewCommentsPanelProps> = ({
  projectId,
  fileId,
  versionId,
  currentTime,
  currentUserId,
  onSeekTo,
  onCommentAdded,
}) => {
  const { t } = useTranslation();
  const [comments, setComments] = useState<ReviewComment[]>([]);
  const [content, setContent] = useState('');
  const [capturedTime, setCapturedTime] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const loadComments = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await fetchComments(projectId, fileId, versionId);
      setComments(data);
    } catch (err) {
      console.error('Failed to load comments:', err);
    } finally {
      setIsLoading(false);
    }
  }, [projectId, fileId, versionId]);

  useEffect(() => {
    loadComments();
  }, [loadComments]);

  const handleSubmit = async () => {
    const trimmed = content.trim();
    if (!trimmed || isSubmitting) return;

    setIsSubmitting(true);
    try {
      await addComment(projectId, fileId, {
        content: trimmed,
        timestamp_seconds: capturedTime,
        version_id: versionId ?? null,
      });
      setContent('');
      setCapturedTime(null);
      await loadComments();
      onCommentAdded();
    } catch (err) {
      console.error('Failed to add comment:', err);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDelete = async (commentId: string) => {
    try {
      await deleteComment(projectId, fileId, commentId);
      await loadComments();
    } catch (err) {
      console.error('Failed to delete comment:', err);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Comment Input */}
      <div className="p-3 border-b border-zinc-700/50">
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t('mediatrack.review.commentPlaceholder')}
          rows={3}
          className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder-zinc-500 resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500"
        />
        <div className="flex items-center justify-between mt-2">
          <div className="flex items-center gap-2">
            {capturedTime !== null && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-500/20 text-indigo-300">
                [{formatTimestamp(capturedTime)}]
                <button
                  onClick={() => setCapturedTime(null)}
                  className="hover:text-indigo-100 transition-colors"
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            )}
            <button
              onClick={() => setCapturedTime(currentTime)}
              className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700 transition-colors"
            >
              <Clock className="w-3.5 h-3.5" />
              {t('mediatrack.review.captureTimestamp')}
            </button>
          </div>
          <button
            onClick={handleSubmit}
            disabled={!content.trim() || isSubmitting}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {isSubmitting ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Send className="w-3.5 h-3.5" />
            )}
          </button>
        </div>
      </div>

      {/* Comment List */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-5 h-5 animate-spin text-zinc-500" />
          </div>
        ) : comments.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-zinc-500">
            <MessageSquare className="w-8 h-8 mb-2 opacity-40" />
            <span className="text-sm">{t('mediatrack.review.noComments')}</span>
          </div>
        ) : (
          <div className="divide-y divide-zinc-800">
            {comments.map((comment) => (
              <ReviewCommentItem
                key={comment.id}
                comment={comment}
                currentUserId={currentUserId}
                onSeekTo={onSeekTo}
                onDelete={handleDelete}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
