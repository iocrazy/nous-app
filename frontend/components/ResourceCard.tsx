import React, { useRef, useCallback } from 'react';
import { File, Film, Image, FileText, FileSpreadsheet, Presentation, FileType, Trash2, RotateCcw, X, Clock, Check, MoreVertical } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ResourceItem, Tag } from '../types';
import { getResourceCoverUrl } from '../services/resourceService';

interface ResourceCardProps {
  item: ResourceItem;
  onClick: (e?: React.MouseEvent) => void;
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
  selectable?: boolean;
  isChecked?: boolean;
  onToggleSelect?: (e: React.MouseEvent) => void;
  // Drag support
  selectedIds?: Set<string>;
  compositeId?: string;
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
  // PDF
  if (mimeType.includes('pdf'))
    return { icon: FileType, color: 'text-red-400', bg: 'bg-red-500/20' };
  // Word / document
  if (mimeType.includes('word') || mimeType.includes('document') || mimeType.includes('msword'))
    return { icon: FileText, color: 'text-blue-400', bg: 'bg-blue-500/20' };
  // Excel / spreadsheet
  if (mimeType.includes('spreadsheet') || mimeType.includes('excel') || mimeType.includes('ms-excel'))
    return { icon: FileSpreadsheet, color: 'text-emerald-400', bg: 'bg-emerald-500/20' };
  // PowerPoint / presentation
  if (mimeType.includes('presentation') || mimeType.includes('powerpoint'))
    return { icon: Presentation, color: 'text-orange-400', bg: 'bg-orange-500/20' };
  // Generic text
  if (mimeType.startsWith('text/'))
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
  selectable = false,
  isChecked = false,
  onToggleSelect,
  selectedIds,
  compositeId,
}) => {
  const { t } = useTranslation();
  const resource = item.resource;
  const filename = resource?.filename ?? 'Untitled';
  const mimeType = resource?.mime_type ?? null;
  const fileSize = resource?.file_size_bytes ?? null;
  const createdAt = resource?.created_at ?? item.created_at;
  const { icon: IconComponent, color, bg } = getFileIcon(mimeType);

  // Determine thumbnail source: thumbnail_path (Supabase URL) > cover endpoint
  const thumbnailSrc = React.useMemo(() => {
    if (resource?.thumbnail_path) return resource.thumbnail_path;
    if (resource?.cover_image_path && resource?.id) {
      return getResourceCoverUrl(String(resource.id));
    }
    return null;
  }, [resource?.thumbnail_path, resource?.cover_image_path, resource?.id]);

  const selectedRing = isSelected ? 'ring-2 ring-indigo-500' : '';
  const checkedRing = isChecked ? 'ring-2 ring-indigo-500' : '';

  // ─── Drag support ────────────────────────────────────
  const handleDragStart = useCallback((e: React.DragEvent) => {
    const myId = compositeId || `item:${item.id}`;
    let dragIds: string[];

    if (selectedIds && selectedIds.has(myId) && selectedIds.size > 1) {
      dragIds = Array.from(selectedIds);
    } else {
      dragIds = [myId];
    }

    e.dataTransfer.setData(
      'application/mediahub-items',
      JSON.stringify({ type: 'resources', ids: dragIds })
    );
    e.dataTransfer.effectAllowed = 'move';

    // Custom drag image showing count
    const dragEl = document.createElement('div');
    dragEl.style.cssText = 'position:fixed;top:-1000px;left:-1000px;background:#3730a3;color:white;padding:6px 14px;border-radius:8px;font-size:13px;font-weight:600;z-index:99999;pointer-events:none;';
    dragEl.textContent = dragIds.length > 1 ? `${dragIds.length} items` : filename;
    document.body.appendChild(dragEl);
    e.dataTransfer.setDragImage(dragEl, 0, 0);
    requestAnimationFrame(() => {
      setTimeout(() => document.body.removeChild(dragEl), 0);
    });
  }, [compositeId, item.id, selectedIds, filename]);

  const checkbox = selectable ? (
    <button
      onClick={(e) => { e.stopPropagation(); onToggleSelect?.(e); }}
      className={`absolute top-2 left-2 z-10 w-5 h-5 rounded flex items-center justify-center transition-all ${
        isChecked
          ? 'bg-indigo-500 text-white'
          : 'bg-zinc-800/80 border border-zinc-600 text-transparent group-hover:text-zinc-400 opacity-0 group-hover:opacity-100'
      } ${isChecked ? 'opacity-100' : ''}`}
    >
      <Check size={12} />
    </button>
  ) : null;

  // ⋮ more button that triggers context menu
  const moreButton = onContextMenu ? (
    <button
      onClick={(e) => { e.stopPropagation(); onContextMenu(e); }}
      className="p-1.5 bg-zinc-900/80 hover:bg-zinc-700 rounded-lg text-zinc-400 hover:text-white transition-colors opacity-0 group-hover:opacity-100"
      title={t('resources.moreActions')}
    >
      <MoreVertical size={14} />
    </button>
  ) : null;

  if (viewMode === 'list') {
    return (
      <div
        data-context-item
        draggable
        onDragStart={handleDragStart}
        onClick={(e) => onClick(e)}
        onContextMenu={onContextMenu}
        className={`flex items-center gap-3 px-4 py-2.5 bg-zinc-800/40 hover:bg-zinc-800 border border-zinc-700/20 hover:border-zinc-600 hover:ring-1 hover:ring-zinc-700 rounded-lg cursor-pointer transition-all duration-200 group relative ${selectedRing} ${checkedRing}`}
      >
        {checkbox}
        <div className={`w-9 h-9 rounded-lg ${bg} flex items-center justify-center flex-shrink-0`}>
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
            <p className="text-sm text-zinc-200 truncate group-hover:text-white transition-colors font-medium">
              {filename}
            </p>
          )}
        </div>
        <span className="text-[11px] text-zinc-500 flex-shrink-0 tabular-nums">
          {formatFileSize(fileSize)}
        </span>
        <span className="text-[11px] text-zinc-600 flex-shrink-0">
          {formatDate(createdAt)}
        </span>
        {/* Actions */}
        <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
          {moreButton}
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
      data-context-item
      draggable
      onDragStart={handleDragStart}
      onClick={(e) => onClick(e)}
      onContextMenu={onContextMenu}
      className={`relative bg-zinc-800/60 hover:bg-zinc-800 border border-zinc-700/30 hover:ring-1 hover:ring-zinc-700 rounded-xl cursor-pointer transition-all duration-200 group overflow-hidden hover:shadow-lg hover:shadow-black/20 ${selectedRing} ${checkedRing}`}
    >
      {checkbox}
      {/* Thumbnail */}
      <div className={`relative h-28 flex items-center justify-center ${bg}`}>
        {thumbnailSrc ? (
          <img
            src={thumbnailSrc}
            alt={filename}
            className="w-full h-full object-cover"
            loading="lazy"
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
          />
        ) : (
          <IconComponent size={36} className={`${color} opacity-50 group-hover:opacity-80 transition-opacity`} />
        )}
        {mimeType?.startsWith('video/') && resource?.duration_seconds != null && (
          <span className="absolute bottom-1.5 right-1.5 bg-black/75 text-white text-[11px] px-1.5 py-0.5 rounded-md font-medium tabular-nums">
            {formatDuration(resource.duration_seconds)}
          </span>
        )}
      </div>
      {/* Actions overlay - top right */}
      <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        {moreButton}
        {!showRestoreAction && onTrash && resource && !moreButton && (
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
      <div className="px-3 py-2.5 border-t border-zinc-700/20">
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
          <p className="text-[13px] text-zinc-200 truncate group-hover:text-white transition-colors font-medium">
            {filename}
          </p>
        )}
        <div className="flex items-center justify-between mt-1 text-[11px] text-zinc-600">
          <span>{formatFileSize(fileSize)}</span>
          <span>{formatDate(createdAt)}</span>
        </div>
      </div>
    </div>
  );
};
