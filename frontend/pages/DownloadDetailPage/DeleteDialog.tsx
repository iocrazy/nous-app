import React from 'react';
import { Loader2, Trash2, X, Image as ImageIcon } from 'lucide-react';
import { Video } from '../../types';
import { getCoverUrl } from '../../utils/awemeType';
import { useAuth } from '../../contexts/AuthContext';

interface DeleteDialogProps {
  video: Video;
  isDeleting: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export function DeleteDialog({ video, isDeleting, onClose, onConfirm }: DeleteDialogProps) {
  const { mediaToken } = useAuth();
  const coverUrl = getCoverUrl(video, mediaToken ?? undefined);
  return (
    <div
      className="fixed inset-0 z-[100] bg-black/80 backdrop-blur-sm animate-in fade-in duration-200"
      style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}
      onClick={onClose}
    >
      <div
        className="bg-ink-900 border border-ink-700 rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden animate-in zoom-in-95 duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-ink-800">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-amber-500/10 rounded-lg">
              <Trash2 className="w-5 h-5 text-amber-500" />
            </div>
            <h3 className="text-lg font-semibold text-white">Move to Trash</h3>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-ink-500 hover:text-white hover:bg-ink-800 rounded-lg transition-colors"
          >
            <X size={18} />
          </button>
        </div>
        <div className="px-5 py-4 space-y-4">
          <p className="text-sm text-ink-400">
            This item will be moved to the Recycle Bin. You can restore it later.
          </p>
          <div className="flex items-center gap-3 p-3 bg-ink-800/50 rounded-lg border border-ink-700/50">
            {coverUrl ? (
              <img
                src={coverUrl}
                alt="Preview"
                className="w-12 h-12 rounded-lg object-cover"
              />
            ) : (
              <div className="w-12 h-12 rounded-lg bg-ink-700 flex items-center justify-center">
                <ImageIcon className="w-5 h-5 text-ink-500" />
              </div>
            )}
            <div className="flex-1 min-w-0">
              <p className="text-sm text-white font-medium truncate">
                {video.title || 'Untitled'}
              </p>
              <p className="text-xs text-ink-500">@{video.author}</p>
            </div>
          </div>
        </div>
        <div className="flex gap-3 px-5 py-4 bg-ink-800/30 border-t border-ink-800">
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2.5 bg-ink-800 hover:bg-ink-700 text-ink-300 rounded-lg font-medium transition-colors border border-ink-700"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={isDeleting}
            className="flex-1 px-4 py-2.5 bg-amber-600 hover:bg-amber-500 text-white rounded-lg font-medium transition-colors disabled:opacity-70 flex items-center justify-center gap-2"
          >
            {isDeleting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Moving...
              </>
            ) : (
              <>
                <Trash2 className="w-4 h-4" />
                Move to Trash
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
