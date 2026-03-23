// frontend/pages/SharePage.tsx

/**
 * Public share page — accessible without authentication.
 *
 * Flow:
 * 1. Extract shareCode from URL
 * 2. If share has password → show password form
 * 3. Call accessShare(shareCode, password?) to get share data
 * 4. Render content based on share_type (link/review/presentation/delivery)
 * 5. Handle error states: expired, cancelled, password error, max views
 */

import React, { useState, useCallback, useEffect, useRef } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Lock,
  Download,
  Eye,
  Clock,
  XCircle,
  Loader2,
  Sparkles,
  FileText,
  Image as ImageIcon,
  Film,
  Music,
  MessageSquare,
  Send,
  Timer,
} from 'lucide-react';
import { accessShare } from '../services/sharesService';
import { Share } from '../types';
import {
  fetchShareComments,
  createShareComment,
  ReviewComment,
} from '../services/reviewCommentsService';

type Phase = 'initial' | 'password' | 'loading' | 'content' | 'error';

const API_BASE = 'VITE_API_URL' in import.meta.env ? (import.meta.env.VITE_API_URL || '') : 'http://localhost:8080';

export const SharePage: React.FC = () => {
  const { shareCode } = useParams<{ shareCode: string }>();
  const { t } = useTranslation();

  const [phase, setPhase] = useState<Phase>('initial');
  const [share, setShare] = useState<Share | null>(null);
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [passwordError, setPasswordError] = useState(false);

  // Review comments state
  const [comments, setComments] = useState<ReviewComment[]>([]);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [commentText, setCommentText] = useState('');
  const [commentTimecode, setCommentTimecode] = useState<number | null>(null);
  const [submittingComment, setSubmittingComment] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  const loadShare = useCallback(async (pwd?: string) => {
    if (!shareCode) return;

    setPhase('loading');
    setError('');
    setPasswordError(false);

    try {
      const data = await accessShare(shareCode, pwd);
      setShare(data);
      setPhase('content');
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Unknown error';

      if (message === 'Password required') {
        setPhase('password');
      } else if (message === 'Incorrect password') {
        setPasswordError(true);
        setPhase('password');
      } else if (message.includes('expired')) {
        setError(t('share.expired'));
        setPhase('error');
      } else if (message.includes('cancelled')) {
        setError(t('share.cancelled'));
        setPhase('error');
      } else if (message.includes('not found')) {
        setError(t('share.notFound'));
        setPhase('error');
      } else {
        setError(message);
        setPhase('error');
      }
    }
  }, [shareCode, t]);

  // Auto-load on mount
  React.useEffect(() => {
    loadShare();
  }, [loadShare]);

  const handlePasswordSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    loadShare(password);
  };

  // ─── Review comments ─────────────────────────────────────
  useEffect(() => {
    if (phase === 'content' && share?.share_type === 'review' && shareCode) {
      setCommentsLoading(true);
      fetchShareComments(shareCode)
        .then(setComments)
        .catch(() => {})
        .finally(() => setCommentsLoading(false));
    }
  }, [phase, share?.share_type, shareCode]);

  const handleSubmitComment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!shareCode || !commentText.trim()) return;

    setSubmittingComment(true);
    try {
      const newComment = await createShareComment(shareCode, {
        content: commentText.trim(),
        timecode: commentTimecode ?? undefined,
      });
      setComments((prev) => [...prev, newComment]);
      setCommentText('');
      setCommentTimecode(null);
    } catch {
      // Comment creation requires auth — silently skip for anonymous users
    } finally {
      setSubmittingComment(false);
    }
  };

  const captureTimecode = () => {
    if (videoRef.current) {
      setCommentTimecode(Math.floor(videoRef.current.currentTime));
    }
  };

  const seekToTimecode = (seconds: number) => {
    if (videoRef.current) {
      videoRef.current.currentTime = seconds;
    }
  };

  const formatTimecode = (seconds: number): string => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
  };

  // ─── File URL helper ────────────────────────────────────
  const getResourceUrl = (resourceId?: string | null): string | null => {
    if (!resourceId) return null;
    return `${API_BASE}/api/v1/resources/${resourceId}/file`;
  };

  const getMediaUrl = (mediaId?: string | null): string | null => {
    if (!mediaId) return null;
    return `${API_BASE}/media/${mediaId}?share_token=${shareCode}`;
  };

  const getCoverMediaUrl = (mediaId?: string | null): string | null => {
    if (!mediaId) return null;
    return `${API_BASE}/media/${mediaId}/cover?share_token=${shareCode}`;
  };

  const isVideoMime = (mime?: string | null) => mime?.startsWith('video/');
  const isImageMime = (mime?: string | null) => mime?.startsWith('image/');
  const isAudioMime = (mime?: string | null) => mime?.startsWith('audio/');

  const getFileTypeIcon = (shareType: string) => {
    switch (shareType) {
      case 'review': return <Film size={48} className="text-indigo-400" />;
      case 'delivery': return <FileText size={48} className="text-emerald-400" />;
      case 'presentation': return <ImageIcon size={48} className="text-amber-400" />;
      default: return <FileText size={48} className="text-zinc-400" />;
    }
  };

  // ─── Renders ────────────────────────────────────────────

  // Loading state
  if (phase === 'loading' || phase === 'initial') {
    return (
      <div className="min-h-screen bg-zinc-950 flex items-center justify-center">
        <div className="text-center">
          <Loader2 size={40} className="animate-spin text-indigo-500 mx-auto mb-4" />
          <p className="text-zinc-400">{t('common.loading')}</p>
        </div>
      </div>
    );
  }

  // Password required
  if (phase === 'password') {
    return (
      <div className="min-h-screen bg-zinc-950 flex items-center justify-center p-4">
        <div className="w-full max-w-sm">
          <div className="text-center mb-8">
            <div className="w-16 h-16 bg-zinc-800 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <Lock size={32} className="text-zinc-400" />
            </div>
            <h1 className="text-xl font-bold text-zinc-100 mb-2">{t('share.passwordRequired')}</h1>
            <p className="text-sm text-zinc-500">{t('share.enterPassword')}</p>
          </div>

          <form onSubmit={handlePasswordSubmit} className="space-y-4">
            <div>
              <input
                type="password"
                value={password}
                onChange={(e) => { setPassword(e.target.value); setPasswordError(false); }}
                placeholder={t('share.enterPassword')}
                autoFocus
                className={`w-full px-4 py-3 bg-zinc-900 border rounded-xl text-zinc-200 placeholder-zinc-600 focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
                  passwordError ? 'border-red-500' : 'border-zinc-700'
                }`}
              />
              {passwordError && (
                <p className="mt-2 text-sm text-red-400">{t('share.passwordError')}</p>
              )}
            </div>
            <button
              type="submit"
              disabled={!password}
              className="w-full py-3 bg-indigo-600 hover:bg-indigo-500 disabled:bg-zinc-800 disabled:text-zinc-600 text-white font-medium rounded-xl transition-colors"
            >
              {t('share.submit')}
            </button>
          </form>
        </div>
      </div>
    );
  }

  // Error state
  if (phase === 'error') {
    return (
      <div className="min-h-screen bg-zinc-950 flex items-center justify-center p-4">
        <div className="text-center">
          <div className="w-16 h-16 bg-zinc-800 rounded-2xl flex items-center justify-center mx-auto mb-4">
            <XCircle size={32} className="text-red-400" />
          </div>
          <h1 className="text-xl font-bold text-zinc-100 mb-2">{error}</h1>
        </div>
      </div>
    );
  }

  // Content view
  if (!share) return null;

  const resourceUrl = getResourceUrl(share.resource_id);
  const shareAny = share as any;
  const mimeType: string | null = shareAny.mime_type || null;
  const mediaId: string | null = shareAny.media_id || null;
  const thumbnailPath: string | null = shareAny.thumbnail_path || null;

  // Prefer /media/{id} route (supports share_token auth), fallback to resource file URL
  const mediaUrl = getMediaUrl(mediaId) || resourceUrl;
  const coverUrl = getCoverMediaUrl(mediaId) || (thumbnailPath ? `${API_BASE}/media/${share.resource_id}/cover?share_token=${shareCode}` : null);
  const isVideo = isVideoMime(mimeType);
  const isImage = isImageMime(mimeType);
  const isAudio = isAudioMime(mimeType);

  return (
    <div className="min-h-screen bg-zinc-950 flex flex-col">
      {/* Header */}
      <header className="border-b border-zinc-800 px-4 sm:px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-8 h-8 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-lg flex items-center justify-center shrink-0">
            <Sparkles className="w-5 h-5 text-white" />
          </div>
          <h1 className="text-lg font-semibold text-zinc-100 truncate">{share.share_name}</h1>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {share.allow_download && resourceUrl && (
            <a
              href={resourceUrl}
              download
              className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium rounded-lg transition-colors"
            >
              <Download size={16} />
              {t('share.download')}
            </a>
          )}
        </div>
      </header>

      {/* Main content */}
      <main className={`flex-1 flex ${share.share_type === 'review' ? 'flex-col md:flex-row' : 'items-center justify-center'} p-4 sm:p-8 gap-6 overflow-hidden`}>
        {/* Preview area */}
        <div className={`${share.share_type === 'review' ? 'flex-1 min-w-0' : 'w-full max-w-4xl'}`}>
          {/* Video preview */}
          {isVideo && mediaUrl ? (
            <div className="bg-black rounded-2xl overflow-hidden h-full">
              <video
                ref={videoRef}
                src={mediaUrl}
                poster={coverUrl || undefined}
                controls
                className="w-full h-full object-contain bg-black"
                style={{ maxHeight: 'calc(100vh - 200px)' }}
              />
            </div>
          ) : isImage && mediaUrl ? (
            /* Image preview */
            <div className="bg-zinc-900 rounded-2xl border border-zinc-800 overflow-hidden flex items-center justify-center">
              <img
                src={mediaUrl}
                alt={share.share_name}
                className="max-w-full max-h-[calc(100vh-200px)] object-contain"
              />
            </div>
          ) : isAudio && mediaUrl ? (
            /* Audio preview */
            <div className="bg-zinc-900 rounded-2xl border border-zinc-800 p-12 flex flex-col items-center justify-center gap-6">
              <Music size={64} className="text-indigo-400" />
              <h2 className="text-lg font-medium text-zinc-200">{share.share_name}</h2>
              <audio src={mediaUrl} controls className="w-full max-w-md" />
            </div>
          ) : resourceUrl ? (
            /* Generic file with download */
            <div className="bg-zinc-900 rounded-2xl border border-zinc-800 overflow-hidden">
              <div className="flex flex-col items-center justify-center py-20">
                {getFileTypeIcon(share.share_type)}
                <h2 className="mt-4 text-lg font-medium text-zinc-200">{share.share_name}</h2>
                <p className="mt-1 text-sm text-zinc-500">{shareAny.filename || 'Shared File'}</p>
                {share.allow_download && (
                  <a
                    href={resourceUrl}
                    download
                    className="mt-6 flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium rounded-lg transition-colors"
                  >
                    <Download size={16} />
                    {t('share.download')}
                  </a>
                )}
              </div>
            </div>
          ) : (
            <div className="text-center py-20">
              {getFileTypeIcon(share.share_type)}
              <h2 className="mt-4 text-lg font-medium text-zinc-200">{share.share_name}</h2>
              <p className="mt-2 text-sm text-zinc-500">{t('share.noPreview')}</p>
            </div>
          )}
        </div>

        {/* Review comments panel */}
        {share.share_type === 'review' && (
          <div className="w-80 flex-shrink-0 bg-zinc-900 border border-zinc-800 rounded-2xl flex flex-col overflow-hidden">
            {/* Panel header */}
            <div className="px-4 py-3 border-b border-zinc-800 flex items-center gap-2">
              <MessageSquare size={16} className="text-zinc-400" />
              <span className="text-sm font-medium text-zinc-200">{t('review.comments')}</span>
              <span className="text-xs text-zinc-500 ml-auto">{comments.length}</span>
            </div>

            {/* Comments list */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {commentsLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 size={20} className="animate-spin text-zinc-500" />
                </div>
              ) : comments.length === 0 ? (
                <div className="text-center py-8">
                  <MessageSquare size={24} className="text-zinc-700 mx-auto mb-2" />
                  <p className="text-sm text-zinc-500">{t('review.noComments')}</p>
                </div>
              ) : (
                comments.map((comment) => (
                  <div key={comment.id} className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-zinc-500">
                        {comment.author_id ? comment.author_id.slice(0, 8) : t('review.anonymous')}
                      </span>
                      <span className="text-xs text-zinc-600">
                        {new Date(comment.created_at).toLocaleString()}
                      </span>
                    </div>
                    {comment.timestamp_seconds != null && (
                      <button
                        onClick={() => seekToTimecode(comment.timestamp_seconds!)}
                        className="text-xs text-indigo-400 hover:text-indigo-300 font-mono"
                      >
                        {formatTimecode(comment.timestamp_seconds)}
                      </button>
                    )}
                    <p className="text-sm text-zinc-300">{comment.content}</p>
                  </div>
                ))
              )}
            </div>

            {/* Comment input */}
            <form onSubmit={handleSubmitComment} className="p-3 border-t border-zinc-800 space-y-2">
              {commentTimecode != null && (
                <div className="flex items-center gap-1.5 text-xs text-indigo-400">
                  <Timer size={12} />
                  <span className="font-mono">{formatTimecode(commentTimecode)}</span>
                  <button
                    type="button"
                    onClick={() => setCommentTimecode(null)}
                    className="text-zinc-500 hover:text-zinc-300 ml-auto"
                  >
                    &times;
                  </button>
                </div>
              )}
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={captureTimecode}
                  title={t('review.timecode')}
                  className="p-2 text-zinc-500 hover:text-indigo-400 hover:bg-zinc-800 rounded-lg transition-colors"
                >
                  <Timer size={16} />
                </button>
                <input
                  type="text"
                  value={commentText}
                  onChange={(e) => setCommentText(e.target.value)}
                  placeholder={t('review.placeholder')}
                  className="flex-1 px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
                <button
                  type="submit"
                  disabled={!commentText.trim() || submittingComment}
                  className="p-2 text-indigo-400 hover:text-indigo-300 disabled:text-zinc-600 transition-colors"
                >
                  <Send size={16} />
                </button>
              </div>
            </form>
          </div>
        )}
      </main>

      {/* Footer metadata */}
      <footer className="border-t border-zinc-800 px-6 py-3 flex items-center justify-center gap-6 text-xs text-zinc-500">
        <span className="flex items-center gap-1">
          <Eye size={12} />
          {share.view_count} {t('share.views')}
        </span>
        <span className="flex items-center gap-1">
          <Clock size={12} />
          {new Date(share.created_at).toLocaleDateString()}
        </span>
      </footer>
    </div>
  );
};
