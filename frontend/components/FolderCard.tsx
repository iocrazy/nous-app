import React, { useState, useCallback, useEffect } from 'react';
import { Folder as FolderIcon, ChevronRight, Check, MoreVertical, Film, Image, FileText, File } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Folder } from '../types';
import { formatDateShort } from '../utils/formatDate';
import { getResourceCoverUrl } from '../services/resourceService';

interface FolderPreviewItem {
  resource_id?: string | null;
  thumbnail_path?: string | null;
  cover_image_path?: string | null;
  mime_type?: string | null;
}

interface FolderCardProps {
  folder: Folder;
  onClick: (e?: React.MouseEvent) => void;
  viewMode: 'grid' | 'list';
  isSelected?: boolean;
  onContextMenu?: (e: React.MouseEvent) => void;
  renaming?: boolean;
  renameValue?: string;
  onRenameChange?: (value: string) => void;
  onRenameConfirm?: () => void;
  onRenameCancel?: () => void;
  childCount?: number;
  selectable?: boolean;
  isChecked?: boolean;
  onToggleSelect?: (e: React.MouseEvent) => void;
  forceShowCheckbox?: boolean;
  // Double-click rename
  onStartRename?: () => void;
  // Double-click to enter folder
  onDoubleClick?: (e?: React.MouseEvent) => void;
  // Drop target
  onDropItems?: (ids: string[]) => void;
  // Folder preview
  previewItems?: FolderPreviewItem[];
}

function formatDate(dateStr: string | null | undefined): string {
  return formatDateShort(dateStr);
}

function getPreviewIcon(mimeType: string | null | undefined) {
  if (!mimeType) return { icon: File, color: 'text-zinc-500' };
  if (mimeType.startsWith('video/')) return { icon: Film, color: 'text-purple-400' };
  if (mimeType.startsWith('image/')) return { icon: Image, color: 'text-green-400' };
  return { icon: FileText, color: 'text-blue-400' };
}

const RenameInput: React.FC<{
  value: string;
  onChange?: (v: string) => void;
  onConfirm?: () => void;
  onCancel?: () => void;
}> = ({ value, onChange, onConfirm, onCancel }) => (
  <input
    autoFocus
    value={value}
    onChange={(e) => onChange?.(e.target.value)}
    onKeyDown={(e) => {
      if (e.key === 'Enter') onConfirm?.();
      if (e.key === 'Escape') onCancel?.();
    }}
    onBlur={() => onCancel?.()}
    onClick={(e) => e.stopPropagation()}
    className="w-full bg-zinc-900 border border-indigo-500 rounded px-2 py-0.5 text-sm text-white focus:outline-none"
  />
);

export const FolderCard: React.FC<FolderCardProps> = ({
  folder,
  onClick,
  viewMode,
  isSelected = false,
  onContextMenu,
  renaming = false,
  renameValue = '',
  onRenameChange,
  onRenameConfirm,
  onRenameCancel,
  childCount,
  onStartRename,
  onDoubleClick,
  selectable = false,
  isChecked = false,
  onToggleSelect,
  forceShowCheckbox = false,
  onDropItems,
  previewItems,
}) => {
  const { t } = useTranslation();
  const [dragHover, setDragHover] = useState(false);
  const dropRing = dragHover ? 'ring-2 ring-indigo-500 bg-indigo-500/10' : '';

  // ─── Drop target handlers ─────────────────────────────
  const handleDragOver = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes('application/mediahub-items')) {
      e.preventDefault();
      e.stopPropagation();
      setDragHover(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.stopPropagation();
    setDragHover(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragHover(false);
    const raw = e.dataTransfer.getData('application/mediahub-items');
    if (raw && onDropItems) {
      try {
        const data = JSON.parse(raw);
        if (data.ids && Array.isArray(data.ids)) {
          onDropItems(data.ids);
        }
      } catch { /* ignore */ }
    }
  }, [onDropItems]);

  const checkbox = selectable ? (
    <button
      onClick={(e) => { e.stopPropagation(); onToggleSelect?.(e); }}
      className={`absolute top-2 left-2 z-10 w-5 h-5 rounded flex items-center justify-center transition-all ${
        isChecked
          ? 'bg-indigo-500 text-white opacity-100'
          : `bg-zinc-800/80 border border-zinc-600 text-transparent group-hover:text-zinc-400 ${forceShowCheckbox ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`
      }`}
    >
      <Check size={12} />
    </button>
  ) : null;

  // ⋮ more button
  const moreButton = onContextMenu ? (
    <button
      onClick={(e) => { e.stopPropagation(); onContextMenu(e); }}
      className="absolute top-2 right-2 z-10 p-1.5 bg-zinc-900/80 hover:bg-zinc-700 rounded-lg text-zinc-400 hover:text-white transition-colors opacity-0 group-hover:opacity-100"
    >
      <MoreVertical size={14} />
    </button>
  ) : null;

  // Folder preview thumbnails
  const hasPreview = previewItems && previewItems.length > 0;

  if (viewMode === 'list') {
    return (
      <div
        data-context-item
        onClick={(e) => onClick(e)}
        onDoubleClick={(e) => { e.stopPropagation(); onDoubleClick?.(e); }}
        onContextMenu={onContextMenu}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`flex items-center gap-3 px-4 py-2.5 hover:bg-zinc-800/60 rounded-lg cursor-pointer transition-[background-color] duration-150 group relative ${
          isChecked || isSelected
            ? 'bg-indigo-500/10 ring-1 ring-inset ring-indigo-500/30'
            : ''
        } ${dropRing}`}
      >
        {checkbox}
        <div className="w-9 h-9 rounded-lg bg-amber-500/10 flex items-center justify-center flex-shrink-0 group-hover:bg-amber-500/20 transition-colors">
          <FolderIcon size={18} className="text-amber-400" fill="currentColor" fillOpacity={0.15} />
        </div>
        <div className="flex-1 min-w-0">
          {renaming ? (
            <RenameInput value={renameValue} onChange={onRenameChange} onConfirm={onRenameConfirm} onCancel={onRenameCancel} />
          ) : (
            <p
              className="text-sm text-zinc-200 truncate group-hover:text-white transition-colors font-medium"
              onDoubleClick={(e) => { e.stopPropagation(); onStartRename?.(); }}
            >
              {folder.name}
            </p>
          )}
        </div>
        {childCount != null && (
          <span className="text-[11px] text-zinc-500 flex-shrink-0 tabular-nums">
            {childCount} {childCount === 1 ? 'item' : 'items'}
          </span>
        )}
        <span className="text-[11px] text-zinc-600 flex-shrink-0">
          {formatDate(folder.created_at)}
        </span>
        <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
          {onContextMenu && (
            <button
              onClick={(e) => { e.stopPropagation(); onContextMenu(e); }}
              className="p-1.5 hover:bg-zinc-700 rounded-lg text-zinc-400 hover:text-white transition-colors"
            >
              <MoreVertical size={14} />
            </button>
          )}
        </div>
        <ChevronRight size={14} className="flex-shrink-0 text-zinc-600 opacity-0 group-hover:opacity-100 transition-opacity" />
      </div>
    );
  }

  // Grid mode — Folder card with tab ear at top
  const previewSlots = previewItems ? previewItems.slice(0, 4) : [];

  return (
    <div
      data-context-item
      onClick={(e) => onClick(e)}
      onDoubleClick={(e) => { e.stopPropagation(); onDoubleClick?.(e); }}
      onContextMenu={onContextMenu}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={`relative cursor-pointer group ${dropRing}`}
    >
      {/* ── Folder tab (ear) ── */}
      <div className="flex items-end">
        <div className="w-[38%] h-3 bg-zinc-800/60 group-hover:bg-zinc-700/80 rounded-t-lg border-t border-l border-r border-zinc-700/30 group-hover:border-amber-500/30" />
        <div className="flex-1" />
      </div>
      {/* ── Card body (flush with tab) ── */}
      <div className="relative bg-zinc-800/60 group-hover:bg-zinc-800 border border-zinc-700/30 group-hover:border-amber-500/30 rounded-b-xl rounded-tr-xl overflow-hidden transition-[background-color,box-shadow] duration-150 hover:shadow-lg hover:shadow-amber-500/5">
        {checkbox}
        {moreButton}
        {/* Preview area — 2x2 thumbnail grid */}
        <div className="relative h-28 bg-zinc-900/80 overflow-hidden">
          {previewSlots.length > 0 ? (
            <div className="grid grid-cols-2 grid-rows-2 gap-[2px] w-full h-full p-[2px]">
              {[0, 1, 2, 3].map((idx) => {
                const pi = previewSlots[idx];
                const hasCover = pi?.resource_id && (pi.thumbnail_path || pi.cover_image_path);
                if (hasCover) {
                  return (
                    <img key={idx} src={getResourceCoverUrl(pi.resource_id!)} alt="" className="w-full h-full object-cover rounded-sm" loading="lazy" />
                  );
                }
                if (pi) {
                  const { icon: PreviewIcon, color: previewColor } = getPreviewIcon(pi.mime_type);
                  return (
                    <div key={idx} className="w-full h-full bg-zinc-800/60 flex items-center justify-center rounded-sm">
                      <PreviewIcon size={18} className={`${previewColor} opacity-50`} />
                    </div>
                  );
                }
                return <div key={idx} className="w-full h-full bg-zinc-800/40 rounded-sm" />;
              })}
            </div>
          ) : (
            <div className="w-full h-full flex items-center justify-center bg-gradient-to-br from-amber-900/15 via-zinc-900/60 to-zinc-900/80">
              <FolderIcon
                size={40}
                className="text-amber-500/60 group-hover:text-amber-400/80 transition-all duration-300 group-hover:scale-105 drop-shadow-lg"
                fill="currentColor"
                fillOpacity={0.25}
              />
            </div>
          )}
          {/* Item count badge */}
          {childCount != null && childCount > 0 && (
            <span className="absolute top-1.5 right-1.5 text-[10px] text-zinc-300 bg-zinc-900/80 backdrop-blur-sm px-1.5 py-0.5 rounded-md font-medium">
              {childCount}
            </span>
          )}
        </div>
        {/* Info */}
        <div className="px-3 py-2 border-t border-zinc-700/20">
          {renaming ? (
            <RenameInput value={renameValue} onChange={onRenameChange} onConfirm={onRenameConfirm} onCancel={onRenameCancel} />
          ) : (
            <p
              className="text-[13px] text-zinc-200 truncate group-hover:text-white transition-colors font-medium"
              onDoubleClick={(e) => { e.stopPropagation(); onStartRename?.(); }}
            >
              {folder.name}
            </p>
          )}
          <p className="text-[11px] text-zinc-600 mt-0.5">
            {formatDate(folder.created_at)}
          </p>
        </div>
      </div>
    </div>
  );
};
