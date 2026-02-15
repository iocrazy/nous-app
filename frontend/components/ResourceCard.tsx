import React from 'react';
import { File, Film, Image, FileText, Trash2, RotateCcw, X, Clock } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ResourceItem, Tag } from '../types';

interface ResourceCardProps {
  item: ResourceItem;
  onClick: () => void;
  viewMode: 'grid' | 'list';
  isSelected?: boolean;
  showRestoreAction?: boolean;
  onTrash?: (resourceId: string) => void;
  onRestore?: (resourceId: string) => void;
  onPermanentDelete?: (resourceId: string) => void;
  onContextMenu?: (e: React.MouseEvent) => void;
  renaming?: boolean;
  renameValue?: string;
  onRenameChange?: (value: string) => void;
  onRenameConfirm?: () => void;
  onRenameCancel?: () => void;
}

function formatFileSize(bytes: number | null | undefined): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '';
  return new Date(dateStr).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function getFileIcon(mimeType: string | null | undefined) {
  if (!mimeType) return { icon: File, color: 'text-zinc-400', bg: 'bg-zinc-500/20' };
  if (mimeType.startsWith('video/')) return { icon: Film, color: 'text-purple-400', bg: 'bg-purple-500/20' };
  if (mimeType.startsWith('image/')) return { icon: Image, color: 'text-green-400', bg: 'bg-green-500/20' };
  if (mimeType.startsWith('text/') || mimeType.includes('pdf') || mimeType.includes('document'))
    return { icon: FileText, color: 'text-blue-400', bg: 'bg-blue-500/20' };
  return { icon: File, color: 'text-zinc-400', bg: 'bg-zinc-500/20' };
}

export const ResourceCard: React.FC<ResourceCardProps> = ({
  item,
  onClick,
  viewMode,
  isSelected = false,
  showRestoreAction = false,
  onTrash,
  onRestore,
  onPermanentDelete,
  onContextMenu,
  renaming = false,
  renameValue = '',
  onRenameChange,
  onRenameConfirm,
  onRenameCancel,
}) => {
  const { t } = useTranslation();
  const resource = item.resource;
  const filename = resource?.filename ?? 'Untitled';
  const mimeType = resource?.mime_type ?? null;
  const fileSize = resource?.file_size_bytes ?? null;
  const createdAt = resource?.created_at ?? item.created_at;
  const { icon: IconComponent, color, bg } = getFileIcon(mimeType);

  const selectedRing = isSelected ? 'ring-2 ring-indigo-500' : '';

  if (viewMode === 'list') {
    return (
      <div
        onClick={onClick}
        onContextMenu={onContextMenu}
        className={`flex items-center gap-4 px-4 py-3 bg-zinc-800/60 hover:bg-zinc-800 border border-zinc-700/30 hover:border-zinc-600 rounded-xl cursor-pointer transition-all duration-200 group ${selectedRing}`}
      >
        <div className={`p-2 rounded-lg ${bg} flex-shrink-0`}>
          <IconComponent size={18} className={color} />
        </div>
        <div className="flex-1 min-w-0">
          {renaming ? (
            <input
              autoFocus
              value={renameValue}
              onChange={(e) => onRenameChange?.(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') onRenameConfirm?.();
                if (e.key === 'Escape') onRenameCancel?.();
              }}
              onBlur={() => onRenameCancel?.()}
              onClick={(e) => e.stopPropagation()}
              className="w-full bg-zinc-900 border border-indigo-500 rounded px-2 py-0.5 text-sm text-white focus:outline-none"
            />
          ) : (
            <p className="text-sm text-white truncate group-hover:text-indigo-300 transition-colors">
              {filename}
            </p>
          )}
        </div>
        <div className="text-xs text-zinc-500 flex-shrink-0">
          {formatFileSize(fileSize)}
        </div>
        <div className="text-xs text-zinc-500 flex-shrink-0 flex items-center gap-1">
          <Clock size={12} />
          {formatDate(createdAt)}
        </div>
        {/* Actions */}
        <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
          {!showRestoreAction && onTrash && resource && (
            <button
              onClick={(e) => { e.stopPropagation(); onTrash(resource.id); }}
              className="p-1.5 hover:bg-red-900/80 rounded-lg text-zinc-400 hover:text-red-400 transition-colors"
              title={t('resources.moveToTrash')}
            >
              <Trash2 size={14} />
            </button>
          )}
          {showRestoreAction && onRestore && resource && (
            <button
              onClick={(e) => { e.stopPropagation(); onRestore(resource.id); }}
              className="p-1.5 hover:bg-emerald-900/80 rounded-lg text-zinc-400 hover:text-emerald-400 transition-colors"
              title={t('resources.restore')}
            >
              <RotateCcw size={14} />
            </button>
          )}
          {showRestoreAction && onPermanentDelete && resource && (
            <button
              onClick={(e) => { e.stopPropagation(); onPermanentDelete(resource.id); }}
              className="p-1.5 hover:bg-red-900/80 rounded-lg text-zinc-400 hover:text-red-400 transition-colors"
              title={t('resources.deletePermanently')}
            >
              <X size={14} />
            </button>
          )}
        </div>
      </div>
    );
  }

  // Grid mode
  return (
    <div
      onClick={onClick}
      onContextMenu={onContextMenu}
      className={`relative bg-zinc-800/80 hover:bg-zinc-800 border border-zinc-700/50 hover:border-zinc-600 rounded-xl cursor-pointer transition-all duration-200 group overflow-hidden ${selectedRing}`}
    >
      {/* Thumbnail */}
      <div className={`relative h-32 flex items-center justify-center ${bg}`}>
        {resource?.thumbnail_path ? (
          <img
            src={resource.thumbnail_path}
            alt={filename}
            className="w-full h-full object-cover rounded-t-lg"
            loading="lazy"
          />
        ) : (
          <IconComponent size={40} className={`${color} opacity-60 group-hover:opacity-100 transition-opacity`} />
        )}
        {mimeType?.startsWith('video/') && resource?.duration_seconds != null && (
          <span className="absolute bottom-1 right-1 bg-black/70 text-white text-xs px-1.5 py-0.5 rounded">
            {formatDuration(resource.duration_seconds)}
          </span>
        )}
      </div>
      {/* Actions overlay */}
      <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        {!showRestoreAction && onTrash && resource && (
          <button
            onClick={(e) => { e.stopPropagation(); onTrash(resource.id); }}
            className="p-1.5 bg-zinc-900/80 hover:bg-red-900/80 rounded-lg text-zinc-400 hover:text-red-400 transition-colors"
            title={t('resources.moveToTrash')}
          >
            <Trash2 size={14} />
          </button>
        )}
        {showRestoreAction && onRestore && resource && (
          <button
            onClick={(e) => { e.stopPropagation(); onRestore(resource.id); }}
            className="p-1.5 bg-zinc-900/80 hover:bg-emerald-900/80 rounded-lg text-zinc-400 hover:text-emerald-400 transition-colors"
            title={t('resources.restore')}
          >
            <RotateCcw size={14} />
          </button>
        )}
        {showRestoreAction && onPermanentDelete && resource && (
          <button
            onClick={(e) => { e.stopPropagation(); onPermanentDelete(resource.id); }}
            className="p-1.5 bg-zinc-900/80 hover:bg-red-900/80 rounded-lg text-zinc-400 hover:text-red-400 transition-colors"
            title={t('resources.deletePermanently')}
          >
            <X size={14} />
          </button>
        )}
      </div>
      {/* Info */}
      <div className="p-3">
        {renaming ? (
          <input
            autoFocus
            value={renameValue}
            onChange={(e) => onRenameChange?.(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') onRenameConfirm?.();
              if (e.key === 'Escape') onRenameCancel?.();
            }}
            onBlur={() => onRenameCancel?.()}
            onClick={(e) => e.stopPropagation()}
            className="w-full bg-zinc-900 border border-indigo-500 rounded px-2 py-0.5 text-sm text-white focus:outline-none"
          />
        ) : (
          <p className="text-sm text-white truncate group-hover:text-indigo-300 transition-colors font-medium">
            {filename}
          </p>
        )}
        <div className="flex items-center justify-between mt-2 text-xs text-zinc-500">
          <span>{formatFileSize(fileSize)}</span>
          <span>{formatDate(createdAt)}</span>
        </div>
      </div>
    </div>
  );
};
