import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Copy, Check, Link2, Lock, Clock, Download } from 'lucide-react';
import { ProjectFile } from '../types';

interface ProjectShareModalProps {
  file: ProjectFile;
  projectId: string;
  isOpen: boolean;
  onClose: () => void;
  onCreated: (share: any) => void;
}

export const ProjectShareModal: React.FC<ProjectShareModalProps> = ({
  file, projectId, isOpen, onClose, onCreated,
}) => {
  const { t } = useTranslation();
  const [shareType, setShareType] = useState<'link' | 'review'>('link');
  const [password, setPassword] = useState('');
  const [usePassword, setUsePassword] = useState(false);
  const [allowDownload, setAllowDownload] = useState(true);
  const [expiresHours, setExpiresHours] = useState<number | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [createdShare, setCreatedShare] = useState<any>(null);
  const [copied, setCopied] = useState(false);

  if (!isOpen) return null;

  const handleCreate = async () => {
    setIsCreating(true);
    try {
      const { createProjectShare } = await import('../services/projectsService');
      const share = await createProjectShare(projectId, {
        file_id: file.id,
        share_type: shareType,
        password: usePassword ? password : undefined,
        allow_download: allowDownload,
        expires_hours: expiresHours || undefined,
      });
      setCreatedShare(share);
      onCreated(share);
    } catch {
      // silent
    } finally {
      setIsCreating(false);
    }
  };

  const shareUrl = createdShare
    ? `${window.location.origin}/share/${createdShare.share_code}`
    : '';

  const handleCopy = () => {
    navigator.clipboard.writeText(shareUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleClose = () => {
    setCreatedShare(null);
    setPassword('');
    setUsePassword(false);
    setExpiresHours(null);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={handleClose}>
      <div
        className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-md mx-4 overflow-hidden shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800">
          <h3 className="text-base font-medium text-zinc-100">
            {t('projects.share.title', 'Share File')}
          </h3>
          <button onClick={handleClose} className="p-1 text-zinc-500 hover:text-zinc-300 transition-colors">
            <X size={18} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {/* File info */}
          <div className="flex items-center gap-3 bg-zinc-800/50 rounded-xl px-3 py-2.5">
            <Link2 size={16} className="text-indigo-400 shrink-0" />
            <span className="text-sm text-zinc-300 truncate">{file.filename}</span>
          </div>

          {!createdShare ? (
            <>
              {/* Share type */}
              <div>
                <label className="text-xs text-zinc-500 mb-1.5 block">
                  {t('projects.share.type', 'Share Type')}
                </label>
                <div className="flex gap-2">
                  {(['link', 'review'] as const).map((type) => (
                    <button
                      key={type}
                      onClick={() => setShareType(type)}
                      className={`flex-1 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                        shareType === type
                          ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30'
                          : 'bg-zinc-800 text-zinc-400 border border-zinc-700 hover:border-zinc-600'
                      }`}
                    >
                      {type === 'link'
                        ? t('projects.share.typeLink', 'Link')
                        : t('projects.share.typeReview', 'Review')}
                    </button>
                  ))}
                </div>
              </div>

              {/* Password */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Lock size={14} className="text-zinc-500" />
                  <span className="text-sm text-zinc-300">
                    {t('projects.share.password', 'Password Protection')}
                  </span>
                </div>
                <button
                  onClick={() => setUsePassword(!usePassword)}
                  className={`w-9 h-5 rounded-full transition-colors relative ${
                    usePassword ? 'bg-indigo-500' : 'bg-zinc-700'
                  }`}
                >
                  <div className={`w-3.5 h-3.5 bg-white rounded-full absolute top-[3px] transition-all ${
                    usePassword ? 'right-[3px]' : 'left-[3px]'
                  }`} />
                </button>
              </div>
              {usePassword && (
                <input
                  type="text"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={t('projects.share.passwordPlaceholder', 'Set a password')}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500"
                />
              )}

              {/* Allow download */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Download size={14} className="text-zinc-500" />
                  <span className="text-sm text-zinc-300">
                    {t('projects.share.allowDownload', 'Allow Download')}
                  </span>
                </div>
                <button
                  onClick={() => setAllowDownload(!allowDownload)}
                  className={`w-9 h-5 rounded-full transition-colors relative ${
                    allowDownload ? 'bg-indigo-500' : 'bg-zinc-700'
                  }`}
                >
                  <div className={`w-3.5 h-3.5 bg-white rounded-full absolute top-[3px] transition-all ${
                    allowDownload ? 'right-[3px]' : 'left-[3px]'
                  }`} />
                </button>
              </div>

              {/* Expiration */}
              <div>
                <div className="flex items-center gap-2 mb-1.5">
                  <Clock size={14} className="text-zinc-500" />
                  <label className="text-sm text-zinc-300">
                    {t('projects.share.expiration', 'Expiration')}
                  </label>
                </div>
                <select
                  value={expiresHours ?? ''}
                  onChange={(e) => setExpiresHours(e.target.value ? Number(e.target.value) : null)}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-indigo-500"
                >
                  <option value="">{t('projects.share.noExpiry', 'Never')}</option>
                  <option value="24">{t('projects.share.expiry24h', '24 hours')}</option>
                  <option value="72">{t('projects.share.expiry3d', '3 days')}</option>
                  <option value="168">{t('projects.share.expiry7d', '7 days')}</option>
                  <option value="720">{t('projects.share.expiry30d', '30 days')}</option>
                </select>
              </div>

              {/* Create button */}
              <button
                onClick={handleCreate}
                disabled={isCreating}
                className="w-full py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 text-white text-sm font-medium rounded-xl transition-colors"
              >
                {isCreating
                  ? t('projects.share.creating', 'Creating...')
                  : t('projects.share.create', 'Create Share Link')}
              </button>
            </>
          ) : (
            /* Share created — show link */
            <div className="space-y-3">
              <p className="text-sm text-green-400">
                {t('projects.share.created', 'Share link created!')}
              </p>
              <div className="flex items-center gap-2">
                <input
                  readOnly
                  value={shareUrl}
                  className="flex-1 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none"
                />
                <button
                  onClick={handleCopy}
                  className="p-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition-colors shrink-0"
                >
                  {copied ? <Check size={16} /> : <Copy size={16} />}
                </button>
              </div>
              {createdShare.password && (
                <p className="text-xs text-zinc-500">
                  {t('projects.share.passwordHint', 'Password: {{password}}', { password: createdShare.password })}
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
