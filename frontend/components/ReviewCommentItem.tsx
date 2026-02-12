import React from 'react';
import { Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ReviewComment } from '../types';

interface ReviewCommentItemProps {
  comment: ReviewComment;
  currentUserId: string;
  onSeekTo: (seconds: number) => void;
  onDelete: (commentId: string) => void;
}

const AVATAR_COLORS = [
  'bg-indigo-500',
  'bg-emerald-500',
  'bg-amber-500',
  'bg-rose-500',
  'bg-cyan-500',
  'bg-purple-500',
];

function hashString(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = (hash << 5) - hash + str.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash);
}

function getAvatarColor(authorId: string): string {
  return AVATAR_COLORS[hashString(authorId) % AVATAR_COLORS.length];
}

function getAvatarLetter(comment: ReviewComment): string {
  if (comment.author_email) {
    return comment.author_email.charAt(0).toUpperCase();
  }
  return comment.author_id.charAt(0).toUpperCase();
}

function formatTimestamp(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function getRelativeTime(dateString: string, t: (key: string, options?: Record<string, unknown>) => string): string {
  const now = Date.now();
  const created = new Date(dateString).getTime();
  const diffMs = now - created;
  const diffSeconds = Math.floor(diffMs / 1000);
  const diffMinutes = Math.floor(diffSeconds / 60);
  const diffHours = Math.floor(diffMinutes / 60);
  const diffDays = Math.floor(diffHours / 24);
  const diffWeeks = Math.floor(diffDays / 7);
  const diffMonths = Math.floor(diffDays / 30);

  if (diffSeconds < 60) {
    return t('mediatrack.review.timeAgo.justNow');
  } else if (diffMinutes < 60) {
    return t('mediatrack.review.timeAgo.minutesAgo', { count: diffMinutes });
  } else if (diffHours < 24) {
    return t('mediatrack.review.timeAgo.hoursAgo', { count: diffHours });
  } else if (diffDays < 7) {
    return t('mediatrack.review.timeAgo.daysAgo', { count: diffDays });
  } else if (diffWeeks < 5) {
    return t('mediatrack.review.timeAgo.weeksAgo', { count: diffWeeks });
  } else {
    return t('mediatrack.review.timeAgo.monthsAgo', { count: diffMonths });
  }
}

function getDisplayName(comment: ReviewComment): string {
  if (comment.author_email) {
    return comment.author_email;
  }
  return comment.author_id.length > 12
    ? `${comment.author_id.slice(0, 12)}...`
    : comment.author_id;
}

const ReviewCommentItem: React.FC<ReviewCommentItemProps> = ({
  comment,
  currentUserId,
  onSeekTo,
  onDelete,
}) => {
  const { t } = useTranslation();
  const isOwn = comment.author_id === currentUserId;

  return (
    <div className="group flex gap-3 px-3 py-2.5 hover:bg-zinc-800/50 rounded-lg transition-colors">
      <div
        className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium text-white ${getAvatarColor(comment.author_id)}`}
      >
        {getAvatarLetter(comment)}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-sm font-medium text-zinc-200 truncate">
            {getDisplayName(comment)}
          </span>

          {comment.timestamp_seconds !== null && (
            <button
              onClick={() => onSeekTo(comment.timestamp_seconds!)}
              className="flex-shrink-0 px-1.5 py-0.5 text-xs font-mono rounded bg-indigo-500/20 text-indigo-300 hover:bg-indigo-500/30 transition-colors cursor-pointer"
              title={t('mediatrack.review.seekTo')}
            >
              {formatTimestamp(comment.timestamp_seconds)}
            </button>
          )}

          <span className="text-xs text-zinc-500 flex-shrink-0">
            {getRelativeTime(comment.created_at, t)}
          </span>

          {isOwn && (
            <button
              onClick={() => onDelete(comment.id)}
              className="flex-shrink-0 ml-auto opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-zinc-700 text-zinc-500 hover:text-red-400 transition-all"
              title={t('mediatrack.review.deleteComment')}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
        </div>

        <p className="text-sm text-zinc-300 whitespace-pre-wrap break-words">
          {comment.content}
        </p>
      </div>
    </div>
  );
};

export { ReviewCommentItem };
