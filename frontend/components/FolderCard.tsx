import React, { useState, useCallback, useEffect } from 'react';
import { Folder as FolderIcon, ChevronRight, Check, MoreVertical, Film, Image, FileText, File } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Folder } from '../types';
import { formatDateShort } from '../utils/formatDate';

interface FolderPreviewItem {
  thumbnail_path?: string | null;
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
  // Double-click rename
  onStartRename?: () => void;
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
  selectable = false,
  isChecked = false,
  onToggleSelect,
  onDropItems,
  previewItems,
}) => {
  const { t } = useTranslation();
  const [dragHover, setDragHover] = useState(false);
  const selectedRing = isSelected ? 'ring-2 ring-indigo-500' : '';
  const checkedRing = isChecked ? 'ring-2 ring-indigo-500' : '';
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
          ? 'bg-indigo-500 text-white'
          : 'bg-zinc-800/80 border border-zinc-600 text-transparent group-hover:text-zinc-400 opacity-0 group-hover:opacity-100'
      } ${isChecked ? 'opacity-100' : ''}`}
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
        onContextMenu={onContextMenu}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`flex items-center gap-3 px-4 py-2.5 bg-zinc-800/40 hover:bg-zinc-800 border border-zinc-700/20 hover:border-zinc-600 hover:ring-1 hover:ring-zinc-700 rounded-lg cursor-pointer transition-all duration-200 group relative ${selectedRing} ${checkedRing} ${dropRing}`}
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

  // Grid mode
  return (
    <div
      data-context-item
      onClick={(e) => onClick(e)}
      onContextMenu={onContextMenu}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={`relative bg-zinc-800/60 hover:bg-zinc-800 border border-zinc-700/30 hover:border-amber-500/30 hover:ring-1 hover:ring-zinc-700 rounded-xl cursor-pointer transition-all duration-200 group overflow-hidden hover:shadow-lg hover:shadow-amber-500/5 ${selectedRing} ${checkedRing} ${dropRing}`}
    >
      {checkbox}
      {moreButton}
      {/* Folder icon area */}
      <div className="relative h-28 flex items-center bg-gradient-to-b from-amber-500/8 to-amber-500/3">
        {/* Left: folder icon */}
        <div className="flex-1 flex items-center justify-center">
          <FolderIcon
            size={52}
            className="text-amber-500 group-hover:text-amber-400 transition-all duration-300 group-hover:scale-105"
            fill="currentColor"
            fillOpacity={0.3}
          />
        </div>
        {/* Right: preview thumbnails */}
        {hasPreview && (
          <div className="w-[40%] flex flex-col gap-1 p-2 h-full justify-center">
            {previewItems!.slice(0, 2).map((pi, idx) => {
              if (pi.thumbnail_path) {
                return (
                  <img
                    key={idx}
                    src={pi.thumbnail_path}
                    alt=""
                    className="rounded object-cover h-[calc(50%-2px)] w-full"
                    loading="lazy"
                  />
                );
              }
              const { icon: PreviewIcon, color: previewColor } = getPreviewIcon(pi.mime_type);
              return (
                <div
                  key={idx}
                  className="rounded bg-zinc-800/60 flex items-center justify-center h-[calc(50%-2px)]"
                >
                  <PreviewIcon size={16} className={`${previewColor} opacity-50`} />
                </div>
              );
            })}
          </div>
        )}
        {childCount != null && childCount > 0 && (
          <span className="absolute top-2.5 right-2.5 text-[10px] text-zinc-500 bg-zinc-800/80 px-1.5 py-0.5 rounded-md">
            {childCount}
          </span>
        )}
      </div>
      {/* Info */}
      <div className="px-3 py-2.5 border-t border-zinc-700/20">
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
        <p className="text-[11px] text-zinc-600 mt-1">
          {formatDate(folder.created_at)}
        </p>
      </div>
    </div>
  );
};
