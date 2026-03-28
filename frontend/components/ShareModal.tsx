import React, { useState } from 'react';
import {
  Link2,
  Eye,
  Presentation,
  Package,
  Lock,
  Calendar,
  Download,
  Droplets,
  Copy,
  Check,
  X,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { DateTimePicker } from './DateTimePicker';
import { createShare } from '../services/sharesService';
import { Share, ShareType } from '../types';

interface ShareModalProps {
  isOpen: boolean;
  onClose: () => void;
  // Target - one of these will be set
  resourceId?: string;
  projectFileId?: string;
  folderId?: string;
  // Defaults
  defaultName?: string;
}

const SHARE_TYPES: { type: ShareType; icon: React.ReactNode; labelKey: string }[] = [
  { type: 'link', icon: <Link2 size={18} />, labelKey: 'shares.link' },
  { type: 'review', icon: <Eye size={18} />, labelKey: 'shares.review' },
  { type: 'presentation', icon: <Presentation size={18} />, labelKey: 'shares.presentation' },
  { type: 'delivery', icon: <Package size={18} />, labelKey: 'shares.delivery' },
];

export const ShareModal: React.FC<ShareModalProps> = ({
  isOpen,
  onClose,
  resourceId,
  projectFileId,
  folderId,
  defaultName = '',
}) => {
  const { t } = useTranslation();

  // Form state
  const [shareType, setShareType] = useState<ShareType>('link');
  const [shareName, setShareName] = useState(defaultName);
  const [passwordEnabled, setPasswordEnabled] = useState(false);
  const [password, setPassword] = useState('');
  const [expirationEnabled, setExpirationEnabled] = useState(false);
  const [expirationDate, setExpirationDate] = useState('');
  const [allowDownload, setAllowDownload] = useState(true);
  const [watermark, setWatermark] = useState(false);

  // UI state
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createdShare, setCreatedShare] = useState<Share | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);

  if (!isOpen) return null;

  const shareLink = createdShare
    ? `${window.location.origin}/share/${createdShare.share_code}`
    : '';

  const handleCreate = async () => {
    setIsCreating(true);
    setError(null);

    try {
      const data: Record<string, unknown> = {
        share_type: shareType,
        share_name: shareName || t('shares.title', 'Share'),
      };

      if (resourceId) data.resource_id = resourceId;
      if (projectFileId) data.project_file_id = projectFileId;
      if (folderId) data.folder_id = folderId;
      if (passwordEnabled && password) data.password = password;
      if (expirationEnabled && expirationDate) data.expires_at = expirationDate;
      data.allow_download = allowDownload;
      data.watermark = watermark;

      const share = await createShare(data as Parameters<typeof createShare>[0]);
      setCreatedShare(share);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create share');
    } finally {
      setIsCreating(false);
    }
  };

  const handleCopyLink = async () => {
    try {
      await navigator.clipboard.writeText(shareLink);
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 2000);
    } catch {
      // Fallback for older browsers
      const textArea = document.createElement('textarea');
      textArea.value = shareLink;
      document.body.appendChild(textArea);
      textArea.select();
      document.execCommand('copy');
      document.body.removeChild(textArea);
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 2000);
    }
  };

  const handleClose = () => {
    // Reset state on close
    setShareType('link');
    setShareName(defaultName);
    setPasswordEnabled(false);
    setPassword('');
    setExpirationEnabled(false);
    setExpirationDate('');
    setAllowDownload(true);
    setWatermark(false);
    setIsCreating(false);
    setError(null);
    setCreatedShare(null);
    setLinkCopied(false);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={handleClose}
      />
      <div className="relative bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl w-full max-w-md max-h-[80vh] overflow-y-auto animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-zinc-800">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-500/20 rounded-lg">
              <Link2 size={20} className="text-indigo-400" />
            </div>
            <h2 className="text-lg font-semibold text-white">
              {t('shares.title', 'Share')}
            </h2>
          </div>
          <button
            onClick={handleClose}
            className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Body */}
        <div className="p-5 space-y-5 max-h-[60vh] overflow-y-auto">
          {createdShare ? (
            /* Success state: show the share link */
            <div className="space-y-4">
              <div className="flex items-center gap-2 p-3 bg-green-500/10 border border-green-500/20 rounded-lg">
                <Check size={16} className="text-green-400 flex-shrink-0" />
                <span className="text-sm text-green-400">
                  {t('shares.shareCreated', 'Share created successfully')}
                </span>
              </div>

              <div className="space-y-2">
                <label className="text-sm text-zinc-400">
                  {t('shares.copyLink', 'Copy Link')}
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    readOnly
                    value={shareLink}
                    className="flex-1 bg-zinc-800 border border-zinc-700 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none"
                  />
                  <button
                    onClick={handleCopyLink}
                    className={`px-4 py-2.5 rounded-xl font-medium text-sm transition-colors flex items-center gap-2 ${
                      linkCopied
                        ? 'bg-green-600 text-white'
                        : 'bg-indigo-600 hover:bg-indigo-500 text-white'
                    }`}
                  >
                    {linkCopied ? (
                      <>
                        <Check size={16} />
                        {t('shares.linkCopied', 'Link Copied!')}
                      </>
                    ) : (
                      <>
                        <Copy size={16} />
                        {t('shares.copyLink', 'Copy Link')}
                      </>
                    )}
                  </button>
                </div>
              </div>
            </div>
          ) : (
            /* Creation form */
            <>
              {/* Share type selector */}
              <div className="space-y-2">
                <label className="text-sm font-medium text-zinc-300">
                  {t('shares.shareType', 'Share Type')}
                </label>
                <div className="grid grid-cols-4 gap-2">
                  {SHARE_TYPES.map(({ type, icon, labelKey }) => (
                    <button
                      key={type}
                      onClick={() => setShareType(type)}
                      className={`flex flex-col items-center gap-1.5 p-3 rounded-xl border transition-colors ${
                        shareType === type
                          ? 'bg-indigo-500/20 border-indigo-500/50 text-indigo-400'
                          : 'bg-zinc-800/50 border-zinc-700 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-300'
                      }`}
                    >
                      {icon}
                      <span className="text-xs font-medium">{t(labelKey)}</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Share name input */}
              <div className="space-y-2">
                <label className="text-sm font-medium text-zinc-300">
                  {t('shares.shareName', 'Share Name')}
                </label>
                <input
                  type="text"
                  value={shareName}
                  onChange={(e) => setShareName(e.target.value)}
                  placeholder={t('shares.shareNamePlaceholder', 'Enter a name for this share')}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded-xl px-4 py-2.5 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-indigo-500/50 transition-colors"
                />
              </div>

              {/* Options */}
              <div className="space-y-3">
                {/* Password protection */}
                <div className="flex items-center justify-between p-3 bg-zinc-800/50 rounded-xl">
                  <div className="flex items-center gap-3">
                    <Lock size={16} className="text-zinc-400" />
                    <span className="text-sm text-zinc-300">
                      {t('shares.password', 'Password Protection')}
                    </span>
                  </div>
                  <button
                    onClick={() => setPasswordEnabled(!passwordEnabled)}
                    className={`relative w-10 h-6 rounded-full transition-colors ${
                      passwordEnabled ? 'bg-indigo-600' : 'bg-zinc-600'
                    }`}
                  >
                    <div
                      className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                        passwordEnabled ? 'translate-x-5' : 'translate-x-1'
                      }`}
                    />
                  </button>
                </div>
                {passwordEnabled && (
                  <input
                    type="text"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder={t('shares.passwordPlaceholder', 'Set a password')}
                    className="w-full bg-zinc-800 border border-zinc-700 rounded-xl px-4 py-2.5 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-indigo-500/50 transition-colors"
                  />
                )}

                {/* Expiration */}
                <div className="flex items-center justify-between p-3 bg-zinc-800/50 rounded-xl">
                  <div className="flex items-center gap-3">
                    <Calendar size={16} className="text-zinc-400" />
                    <span className="text-sm text-zinc-300">
                      {t('shares.expiration', 'Expiration')}
                    </span>
                  </div>
                  <button
                    onClick={() => setExpirationEnabled(!expirationEnabled)}
                    className={`relative w-10 h-6 rounded-full transition-colors ${
                      expirationEnabled ? 'bg-indigo-600' : 'bg-zinc-600'
                    }`}
                  >
                    <div
                      className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                        expirationEnabled ? 'translate-x-5' : 'translate-x-1'
                      }`}
                    />
                  </button>
                </div>
                {expirationEnabled && (
                  <DateTimePicker
                    value={expirationDate}
                    onChange={setExpirationDate}
                  />
                )}

                {/* Allow download */}
                <div className="flex items-center justify-between p-3 bg-zinc-800/50 rounded-xl">
                  <div className="flex items-center gap-3">
                    <Download size={16} className="text-zinc-400" />
                    <span className="text-sm text-zinc-300">
                      {t('shares.allowDownload', 'Allow Download')}
                    </span>
                  </div>
                  <button
                    onClick={() => setAllowDownload(!allowDownload)}
                    className={`relative w-10 h-6 rounded-full transition-colors ${
                      allowDownload ? 'bg-indigo-600' : 'bg-zinc-600'
                    }`}
                  >
                    <div
                      className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                        allowDownload ? 'translate-x-5' : 'translate-x-1'
                      }`}
                    />
                  </button>
                </div>

                {/* Watermark */}
                <div className="flex items-center justify-between p-3 bg-zinc-800/50 rounded-xl">
                  <div className="flex items-center gap-3">
                    <Droplets size={16} className="text-zinc-400" />
                    <span className="text-sm text-zinc-300">
                      {t('shares.watermark', 'Watermark')}
                    </span>
                  </div>
                  <button
                    onClick={() => setWatermark(!watermark)}
                    className={`relative w-10 h-6 rounded-full transition-colors ${
                      watermark ? 'bg-indigo-600' : 'bg-zinc-600'
                    }`}
                  >
                    <div
                      className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                        watermark ? 'translate-x-5' : 'translate-x-1'
                      }`}
                    />
                  </button>
                </div>
              </div>

              {/* Error message */}
              {error && (
                <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg">
                  <X size={16} className="text-red-400 flex-shrink-0" />
                  <span className="text-sm text-red-400">{error}</span>
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex gap-3 p-5 border-t border-zinc-800">
          {createdShare ? (
            <button
              type="button"
              onClick={handleClose}
              className="flex-1 px-4 py-3 text-white bg-zinc-800 hover:bg-zinc-700 rounded-xl font-medium transition-colors"
            >
              {t('common.cancel', 'Close')}
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={handleClose}
                className="flex-1 px-4 py-3 text-zinc-300 bg-zinc-800 hover:bg-zinc-700 rounded-xl font-medium transition-colors"
              >
                {t('common.cancel', 'Cancel')}
              </button>
              <button
                type="button"
                onClick={handleCreate}
                disabled={isCreating}
                className="flex-1 px-4 py-3 text-white bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 disabled:cursor-not-allowed rounded-xl font-medium transition-colors"
              >
                {isCreating
                  ? t('common.creating', 'Creating...')
                  : t('shares.createShare', 'Create Share')}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
