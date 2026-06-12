import React, { useRef, useCallback, useState } from 'react';
import { File, Film, Image, Music, FileText, FileSpreadsheet, Presentation, FileType, Trash2, RotateCcw, X, Clock, Check, MoreVertical, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ResourceItem, Tag } from '../types';
import { getResourceCoverUrl, getPreviewSpriteUrl } from '../services/resourceService';
import { formatDateShort } from '../utils/formatDate';

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
  forceShowCheckbox?: boolean;
  // Double-click rename
  onStartRename?: () => void;
  // Double-click to open detail
  onDoubleClick?: (e?: React.MouseEvent) => void;
  // Drag support
  selectedIds?: Set<string>;
  compositeId?: string;
  // Transcode indicator
  isTranscoding?: boolean;
  // Justified view: real aspect ratio (w/h) for the thumbnail (no letterbox bars)
  aspectRatio?: number;
}

function formatFileSize(bytes: number | null | undefined): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function formatDate(dateStr: string | null | undefined): string {
  return formatDateShort(dateStr);
}

const TRASH_RETENTION_DAYS = 15;

function getDaysUntilDeletion(trashedAt: string | null | undefined): number | null {
  if (!trashedAt) return null;
  const trashedDate = new Date(trashedAt);
  const deleteDate = new Date(trashedDate.getTime() + TRASH_RETENTION_DAYS * 24 * 60 * 60 * 1000);
  const now = new Date();
  const remaining = Math.ceil((deleteDate.getTime() - now.getTime()) / (24 * 60 * 60 * 1000));
  return Math.max(0, remaining);
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
  if (mimeType.startsWith('audio/')) return { icon: Music, color: 'text-cyan-400', bg: 'bg-cyan-500/20' };
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
  onStartRename,
  onDoubleClick,
  selectable = false,
  isChecked = false,
  onToggleSelect,
  forceShowCheckbox = false,
  selectedIds,
  compositeId,
  isTranscoding = false,
  aspectRatio,
}) => {
  const { t } = useTranslation();
  const resource = item.resource;
  const filename = resource?.filename ?? 'Untitled';
  const mimeType = resource?.mime_type ?? null;
  const fileSize = resource?.file_size_bytes ?? null;
  const createdAt = resource?.created_at ?? item.created_at;
  const { icon: IconComponent, color, bg } = getFileIcon(mimeType);

  // Determine thumbnail source: always use the cover API endpoint. The
  // backend falls through thumbnail_path > cover_image_path >
  // parsed_media.cover_download_path > (for image/* mime) the original file —
  // so an UPLOADED image with none of the cover signals still previews via its
  // own file. Attempt the request whenever any signal OR the mime is image/*.
  const isImage = mimeType?.startsWith('image/') ?? false;
  const thumbnailSrc = React.useMemo(() => {
    if (
      (resource?.thumbnail_path ||
        resource?.cover_image_path ||
        resource?.media_id ||
        isImage) &&
      resource?.id
    ) {
      return getResourceCoverUrl(String(resource.id));
    }
    return null;
  }, [
    resource?.thumbnail_path,
    resource?.cover_image_path,
    resource?.media_id,
    isImage,
    resource?.id,
  ]);

  // Hover scrub state for video cards
  const isVideo = mimeType?.startsWith('video/') ?? false;
  const [isHovering, setIsHovering] = useState(false);
  const [spriteLoaded, setSpriteLoaded] = useState(false);
  const [spriteError, setSpriteError] = useState(false);
  const [scrubPercent, setScrubPercent] = useState(0);
  const thumbRef = useRef<HTMLDivElement>(null);
  const spriteImgRef = useRef<HTMLImageElement | null>(null);

  const spriteUrl = React.useMemo(() => {
    if (isVideo && resource?.id) return getPreviewSpriteUrl(String(resource.id));
    return null;
  }, [isVideo, resource?.id]);

  // Preload sprite on first hover
  const handleThumbMouseEnter = useCallback(() => {
    if (!isVideo || spriteError) return;
    setIsHovering(true);
    if (!spriteImgRef.current && spriteUrl) {
      const img = new window.Image();
      img.onload = () => { spriteImgRef.current = img; setSpriteLoaded(true); };
      img.onerror = () => { setSpriteError(true); };
      img.src = spriteUrl;
    }
  }, [isVideo, spriteUrl, spriteError]);

  const handleThumbMouseLeave = useCallback(() => {
    setIsHovering(false);
    setScrubPercent(0);
  }, []);

  const handleThumbMouseMove = useCallback((e: React.MouseEvent) => {
    if (!isHovering || !spriteLoaded || !thumbRef.current) return;
    const rect = thumbRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
    setScrubPercent(x / rect.width);
  }, [isHovering, spriteLoaded]);

  // Compute sprite frame style with object-contain behavior.
  // Uses a frame-sized inner div (clipped by overflow) centered in the container.
  const spriteFrame = React.useMemo(() => {
    if (!spriteLoaded || !spriteImgRef.current || !thumbRef.current) return null;

    const FRAME_COUNT = 10;
    const img = spriteImgRef.current;
    const frameW = img.naturalWidth / FRAME_COUNT;
    const frameH = img.naturalHeight;
    const cW = thumbRef.current.offsetWidth;
    const cH = thumbRef.current.offsetHeight;
    if (!frameW || !frameH || !cW || !cH) return null;

    // Contain: scale frame to fit within the container
    const scale = Math.min(cW / frameW, cH / frameH);
    const rfw = frameW * scale;
    const rfh = frameH * scale;

    const frameIndex = Math.min(Math.floor(scrubPercent * FRAME_COUNT), FRAME_COUNT - 1);

    return {
      width: rfw,
      height: rfh,
      style: {
        width: `${rfw}px`,
        height: `${rfh}px`,
        backgroundImage: `url(${spriteUrl})`,
        backgroundSize: `${rfw * FRAME_COUNT}px ${rfh}px`,
        backgroundPosition: `${-frameIndex * rfw}px 0px`,
        backgroundRepeat: 'no-repeat',
      } as React.CSSProperties,
    };
  }, [spriteLoaded, scrubPercent, spriteUrl]);


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

    // Use the card element itself as the drag image (like Kanban drag)
    const card = e.currentTarget as HTMLElement;
    const rect = card.getBoundingClientRect();

    if (dragIds.length > 1) {
      // Multi-select: add a temporary count badge
      const badge = document.createElement('div');
      badge.className = '__drag-badge';
      badge.style.cssText = 'position:absolute;top:-8px;right:-8px;background:#6366f1;color:white;border-radius:999px;min-width:24px;height:24px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;font-family:system-ui,sans-serif;padding:0 6px;z-index:10;box-shadow:0 2px 8px rgba(0,0,0,0.3);';
      badge.textContent = String(dragIds.length);
      card.style.position = 'relative';
      card.appendChild(badge);
      requestAnimationFrame(() => {
        setTimeout(() => badge.remove(), 0);
      });
    }

    e.dataTransfer.setDragImage(card, e.clientX - rect.left, e.clientY - rect.top);
  }, [compositeId, item.id, selectedIds, filename]);

  const checkbox = selectable ? (
    <div className={`absolute top-2 left-2 z-10 ${forceShowCheckbox || isChecked ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'} transition-opacity`}>
      <button
        onClick={(e) => { e.stopPropagation(); onToggleSelect?.(e); }}
        className={`w-6 h-6 rounded-full flex items-center justify-center transition-colors ${
          isChecked
            ? 'bg-indigo-500 text-white shadow-lg'
            : 'bg-black/50 border border-zinc-400 text-transparent hover:border-zinc-200'
        }`}
      >
        <Check size={12} />
      </button>
    </div>
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
        onDoubleClick={(e) => { e.stopPropagation(); onDoubleClick?.(e); }}
        onContextMenu={onContextMenu}
        className={`flex items-center gap-3 px-4 py-2.5 hover:bg-zinc-800/60 rounded-lg cursor-pointer transition-[background-color] duration-150 group relative ${
          isChecked || isSelected
            ? 'bg-indigo-500/10 ring-1 ring-inset ring-indigo-500/30'
            : ''
        }`}
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
            <p
              className="text-sm text-zinc-200 truncate group-hover:text-white transition-colors font-medium select-none cursor-default"
              onDoubleClick={(e) => { e.stopPropagation(); onStartRename?.(); }}
            >
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
        {showRestoreAction && resource?.trashed_at && (() => {
          const days = getDaysUntilDeletion(resource.trashed_at);
          if (days === null) return null;
          return (
            <span className={`text-[11px] flex-shrink-0 flex items-center gap-1 ${days <= 3 ? 'text-red-400' : days <= 7 ? 'text-amber-400' : 'text-zinc-500'}`}>
              <Clock size={11} />
              {t('resources.daysLeft', { count: days })}
            </span>
          );
        })()}
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
      onDoubleClick={(e) => { e.stopPropagation(); onDoubleClick?.(e); }}
      onContextMenu={onContextMenu}
      className={`relative hover:bg-zinc-800 border rounded-xl cursor-pointer transition-[background-color,box-shadow,transform] duration-150 group overflow-hidden hover:shadow-lg hover:shadow-black/20 hover:-translate-y-0.5 ${
        isChecked || isSelected
          ? 'bg-indigo-500/10 border-indigo-500/30 shadow-[0_0_0_1px_rgba(99,102,241,0.3)]'
          : 'bg-zinc-800/60 border-zinc-700/30 hover:border-zinc-600'
      }`}
    >
      {checkbox}
      {/* Thumbnail + Hover Scrub */}
      <div
        ref={thumbRef}
        className={`relative flex items-center justify-center ${aspectRatio == null ? 'h-28 bg-black' : ''} ${!thumbnailSrc ? bg : ''}`}
        style={aspectRatio == null ? undefined : { aspectRatio }}
        onMouseEnter={handleThumbMouseEnter}
        onMouseLeave={handleThumbMouseLeave}
        onMouseMove={handleThumbMouseMove}
      >
        {thumbnailSrc ? (
          <img
            src={thumbnailSrc}
            alt={filename}
            className={`w-full h-full ${aspectRatio == null ? 'object-contain' : 'object-cover'} transition-opacity duration-150 ${isHovering && spriteLoaded ? 'opacity-0' : 'opacity-100'}`}
            loading="lazy"
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
          />
        ) : (
          <IconComponent size={36} className={`${color} opacity-50 group-hover:opacity-80 transition-opacity`} />
        )}
        {/* Sprite scrub overlay — contain-style: portrait frames get side bars */}
        {isHovering && spriteLoaded && spriteFrame && (
          <div className="absolute inset-0 bg-black flex items-center justify-center">
            <div style={spriteFrame.style} />
          </div>
        )}
        {/* Scrub progress bar */}
        {isHovering && spriteLoaded && (
          <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-black/30">
            <div className="h-full bg-white/80 transition-none" style={{ width: `${scrubPercent * 100}%` }} />
          </div>
        )}
        {(mimeType?.startsWith('video/') || mimeType?.startsWith('audio/')) && resource?.duration_seconds != null && (
          <span className={`absolute bottom-1.5 right-1.5 bg-black/75 text-white text-[11px] px-1.5 py-0.5 rounded-md font-medium tabular-nums ${isHovering && spriteLoaded ? 'hidden' : ''}`}>
            {isHovering && spriteLoaded
              ? formatDuration(Math.floor(scrubPercent * resource.duration_seconds))
              : formatDuration(resource.duration_seconds)}
          </span>
        )}
        {/* Scrub timestamp */}
        {isHovering && spriteLoaded && resource?.duration_seconds != null && (
          <span className="absolute bottom-1.5 right-1.5 bg-black/75 text-white text-[11px] px-1.5 py-0.5 rounded-md font-medium tabular-nums">
            {formatDuration(Math.floor(scrubPercent * resource.duration_seconds))}
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
          <p
            className="text-[13px] text-zinc-200 truncate group-hover:text-white transition-colors font-medium select-none cursor-default"
            onDoubleClick={(e) => { e.stopPropagation(); onStartRename?.(); }}
          >
            {filename}
          </p>
        )}
        {isTranscoding && (
          <div className="flex items-center gap-1.5 mt-1">
            <Loader2 size={11} className="text-amber-400 animate-spin" />
            <span className="text-[11px] text-amber-400 animate-pulse">Transcoding...</span>
          </div>
        )}
        {showRestoreAction && resource?.trashed_at ? (() => {
          const days = getDaysUntilDeletion(resource.trashed_at);
          if (days === null) return null;
          return (
            <div className={`flex items-center gap-1 mt-1 text-[11px] ${days <= 3 ? 'text-red-400' : days <= 7 ? 'text-amber-400' : 'text-zinc-500'}`}>
              <Clock size={11} />
              {t('resources.daysLeft', { count: days })}
            </div>
          );
        })() : (
          <div className="flex items-center justify-between mt-1 text-[11px] text-zinc-600">
            <span>{formatFileSize(fileSize)}</span>
            <span>{formatDate(createdAt)}</span>
          </div>
        )}
      </div>
    </div>
  );
};
