import React from 'react';
import { Folder as FolderIcon, ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Folder } from '../types';

interface FolderCardProps {
  folder: Folder;
  onClick: () => void;
  viewMode: 'grid' | 'list';
  isSelected?: boolean;
  onContextMenu?: (e: React.MouseEvent) => void;
  renaming?: boolean;
  renameValue?: string;
  onRenameChange?: (value: string) => void;
  onRenameConfirm?: () => void;
  onRenameCancel?: () => void;
  childCount?: number;
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '';
  return new Date(dateStr).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
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
}) => {
  const { t } = useTranslation();
  const selectedRing = isSelected ? 'ring-2 ring-indigo-500' : '';

  if (viewMode === 'list') {
    return (
      <div
        onClick={onClick}
        onContextMenu={onContextMenu}
        className={`flex items-center gap-3 px-4 py-2.5 bg-zinc-800/40 hover:bg-zinc-800 border border-zinc-700/20 hover:border-zinc-600 rounded-lg cursor-pointer transition-all duration-200 group ${selectedRing}`}
      >
        <div className="w-9 h-9 rounded-lg bg-amber-500/10 flex items-center justify-center flex-shrink-0 group-hover:bg-amber-500/20 transition-colors">
          <FolderIcon size={18} className="text-amber-400" fill="currentColor" fillOpacity={0.15} />
        </div>
        <div className="flex-1 min-w-0">
          {renaming ? (
            <RenameInput value={renameValue} onChange={onRenameChange} onConfirm={onRenameConfirm} onCancel={onRenameCancel} />
          ) : (
            <p className="text-sm text-zinc-200 truncate group-hover:text-white transition-colors font-medium">
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
        <ChevronRight size={14} className="flex-shrink-0 text-zinc-600 opacity-0 group-hover:opacity-100 transition-opacity" />
      </div>
    );
  }

  // Grid mode
  return (
    <div
      onClick={onClick}
      onContextMenu={onContextMenu}
      className={`relative bg-zinc-800/60 hover:bg-zinc-800 border border-zinc-700/30 hover:border-amber-500/30 rounded-xl cursor-pointer transition-all duration-200 group overflow-hidden hover:shadow-lg hover:shadow-amber-500/5 ${selectedRing}`}
    >
      {/* Folder icon area */}
      <div className="relative h-28 flex items-center justify-center bg-gradient-to-b from-amber-500/8 to-amber-500/3">
        <div className="relative">
          <FolderIcon
            size={44}
            className="text-amber-400/50 group-hover:text-amber-400/80 transition-all duration-300 group-hover:scale-105"
            fill="currentColor"
            fillOpacity={0.08}
          />
        </div>
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
          <p className="text-[13px] text-zinc-200 truncate group-hover:text-white transition-colors font-medium">
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
