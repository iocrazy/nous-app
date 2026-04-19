import React, { useEffect } from 'react';
import {
  Eye,
  ExternalLink,
  Download,
  Music,
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
  onRename,
  onShare,
  onCopyLink,
  onDelete,
}) => {
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
      className="fixed z-[100] bg-zinc-900 border border-zinc-700 rounded-lg shadow-2xl overflow-hidden py-1 min-w-[180px]"
      style={{ left: contextMenu.x, top: contextMenu.y }}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        onClick={onViewDetails}
        className="w-full px-3 py-2 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
      >
        <Eye size={14} className="text-zinc-500" />
        View Details
      </button>
      <button
        onClick={onOpenNewTab}
        className="w-full px-3 py-2 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
      >
        <ExternalLink size={14} className="text-zinc-500" />
        Open in New Tab
      </button>
      <div className="border-t border-zinc-800 my-1" />
      <button
        onClick={onDownloadVideo}
        className="w-full px-3 py-2 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
      >
        <Download size={14} className="text-zinc-500" />
        Download Original
      </button>
      {contextMenu.video.music_download_path && (
        <button
          onClick={onDownloadAudio}
          className="w-full px-3 py-2 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
        >
          <Music size={14} className="text-zinc-500" />
          Download Audio
        </button>
      )}
      <div className="border-t border-zinc-800 my-1" />
      <button
        onClick={onRename}
        className="w-full px-3 py-2 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
      >
        <Pencil size={14} className="text-zinc-500" />
        Rename
      </button>
      <button
        onClick={onShare}
        className="w-full px-3 py-2 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
      >
        <Share2 size={14} className="text-zinc-500" />
        Share
      </button>
      <button
        onClick={onCopyLink}
        className="w-full px-3 py-2 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
      >
        <Link size={14} className="text-zinc-500" />
        Copy Link
      </button>
      <div className="border-t border-zinc-800 my-1" />
      <button
        onClick={onDelete}
        className="w-full px-3 py-2 text-left text-sm text-red-400 hover:bg-red-900/20 hover:text-red-300 flex items-center gap-2.5 transition-colors"
      >
        <Trash2 size={14} />
        Delete
      </button>
    </div>
  );
};
