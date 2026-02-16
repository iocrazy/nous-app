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

import React, { useState, useCallback } from 'react';
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
} from 'lucide-react';
import { accessShare } from '../services/sharesService';
import { Share } from '../types';

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

  // ─── File URL helper ────────────────────────────────────
  const getResourceUrl = (resourceId?: string | null): string | null => {
    if (!resourceId) return null;
    return `${API_BASE}/api/v1/resources/${resourceId}/file`;
  };

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

  return (
    <div className="min-h-screen bg-zinc-950 flex flex-col">
      {/* Header */}
      <header className="border-b border-zinc-800 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-lg flex items-center justify-center">
            <Sparkles className="w-5 h-5 text-white" />
          </div>
          <h1 className="text-lg font-semibold text-zinc-100">{share.share_name}</h1>
        </div>
        <div className="flex items-center gap-3">
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
      <main className="flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-4xl">
          {resourceUrl ? (
            <div className="bg-zinc-900 rounded-2xl border border-zinc-800 overflow-hidden">
              {/* Simplified preview - shows file type icon + download option */}
              <div className="flex flex-col items-center justify-center py-20">
                {getFileTypeIcon(share.share_type)}
                <h2 className="mt-4 text-lg font-medium text-zinc-200">{share.share_name}</h2>
                <p className="mt-1 text-sm text-zinc-500">
                  {share.share_type === 'review' ? 'Review Link' :
                   share.share_type === 'delivery' ? 'File Delivery' :
                   share.share_type === 'presentation' ? 'Presentation' : 'Shared Link'}
                </p>
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
