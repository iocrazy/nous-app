import React, { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Eye,
  ExternalLink,
  Download,
  Music,
  Bot,
  Pencil,
  Share2,
  Link,
  Trash2,
} from 'lucide-react';
import { Video } from '../../types';

export interface ContextMenuState {
  x: number;
  y: number;
  video: Video;
}

interface DownloadContextMenuProps {
  contextMenu: ContextMenuState | null;
  onClose: () => void;
  onViewDetails: () => void;
  onOpenNewTab: () => void;
  onDownloadVideo: () => void;
  onDownloadAudio: () => void;
  onSendToAgent: () => void;
  onRename: () => void;
  onShare: () => void;
  onCopyLink: () => void;
  onDelete: () => void;
  onNavigate: (path: string) => void;
}

export const DownloadContextMenu: React.FC<DownloadContextMenuProps> = ({
  contextMenu,
  onClose,
  onViewDetails,
  onOpenNewTab,
  onDownloadVideo,
  onDownloadAudio,
  onSendToAgent,
  onRename,
  onShare,
  onCopyLink,
  onDelete,
}) => {
  // Labels reuse the resource library's `resources.*` keys wherever the item
  // is the same action, so both context menus stay worded identically.
  const { t } = useTranslation();

  useEffect(() => {
    if (!contextMenu) return;
    const close = () => onClose();
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    document.addEventListener('click', close);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('click', close);
      document.removeEventListener('keydown', handleKey);
    };
  }, [contextMenu, onClose]);

  if (!contextMenu) return null;

  return (
    <div
      className="fixed z-[100] bg-ink-900 border border-ink-700 rounded-lg shadow-2xl overflow-hidden py-1 min-w-[180px]"
      style={{ left: contextMenu.x, top: contextMenu.y }}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        onClick={onViewDetails}
        className="w-full px-3 py-2 text-left text-sm text-ink-300 hover:bg-ink-800 hover:text-ink-50 flex items-center gap-2.5 transition-colors"
      >
        <Eye size={14} className="text-ink-500" />
        {t('resources.viewDetails', 'View Details')}
      </button>
      <button
        onClick={onOpenNewTab}
        className="w-full px-3 py-2 text-left text-sm text-ink-300 hover:bg-ink-800 hover:text-ink-50 flex items-center gap-2.5 transition-colors"
      >
        <ExternalLink size={14} className="text-ink-500" />
        {t('resources.openInNewTab', 'Open in New Tab')}
      </button>
      <div className="border-t border-ink-800 my-1" />
      <button
        onClick={onDownloadVideo}
        className="w-full px-3 py-2 text-left text-sm text-ink-300 hover:bg-ink-800 hover:text-ink-50 flex items-center gap-2.5 transition-colors"
      >
        <Download size={14} className="text-ink-500" />
        {t('resources.downloadOriginal', 'Download Original')}
      </button>
      {contextMenu.video.music_download_path && (
        <button
          onClick={onDownloadAudio}
          className="w-full px-3 py-2 text-left text-sm text-ink-300 hover:bg-ink-800 hover:text-ink-50 flex items-center gap-2.5 transition-colors"
        >
          <Music size={14} className="text-ink-500" />
          {t('resources.downloadAudio', 'Download Audio')}
        </button>
      )}
      <div className="border-t border-ink-800 my-1" />
      {/* Send to Agent: same action, same wording, same chain as the resource
          library's context menu (utils/sendResourceToAgent). It was missing
          here for a release, which hid the feature from the view holding
          most of the user's media. */}
      <button
        onClick={onSendToAgent}
        className="w-full px-3 py-2 text-left text-sm text-ink-300 hover:bg-ink-800 hover:text-ink-50 flex items-center gap-2.5 transition-colors"
      >
        <Bot size={14} className="text-ink-500" />
        {t('resources.sendToAgent', 'Send to Agent')}
      </button>
      <div className="border-t border-ink-800 my-1" />
      <button
        onClick={onRename}
        className="w-full px-3 py-2 text-left text-sm text-ink-300 hover:bg-ink-800 hover:text-ink-50 flex items-center gap-2.5 transition-colors"
      >
        <Pencil size={14} className="text-ink-500" />
        {t('resources.rename', 'Rename')}
      </button>
      <button
        onClick={onShare}
        className="w-full px-3 py-2 text-left text-sm text-ink-300 hover:bg-ink-800 hover:text-ink-50 flex items-center gap-2.5 transition-colors"
      >
        <Share2 size={14} className="text-ink-500" />
        {t('resources.share', 'Share')}
      </button>
      <button
        onClick={onCopyLink}
        className="w-full px-3 py-2 text-left text-sm text-ink-300 hover:bg-ink-800 hover:text-ink-50 flex items-center gap-2.5 transition-colors"
      >
        <Link size={14} className="text-ink-500" />
        {t('resources.copyLink', 'Copy Link')}
      </button>
      <div className="border-t border-ink-800 my-1" />
      <button
        onClick={onDelete}
        className="w-full px-3 py-2 text-left text-sm text-red-400 hover:bg-red-900/20 hover:text-red-300 flex items-center gap-2.5 transition-colors"
      >
        <Trash2 size={14} />
        {t('resources.delete', 'Delete')}
      </button>
    </div>
  );
};
