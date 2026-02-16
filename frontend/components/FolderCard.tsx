import React from 'react';
import { FolderOpen, Clock, ChevronRight } from 'lucide-react';
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
        className={`flex items-center gap-4 px-4 py-3 bg-zinc-800/60 hover:bg-zinc-800 border border-zinc-700/30 hover:border-zinc-600 rounded-xl cursor-pointer transition-all duration-200 group ${selectedRing}`}
      >
        <div className="p-2 rounded-lg bg-amber-500/20 flex-shrink-0">
          <FolderOpen size={18} className="text-amber-400" />
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
            <p className="text-sm text-white truncate group-hover:text-amber-300 transition-colors">
              {folder.name}
            </p>
          )}
        </div>
        {childCount != null && (
          <div className="text-xs text-zinc-500 flex-shrink-0">
            {t('resources.itemCount', { count: childCount })}
          </div>
        )}
        <div className="text-xs text-zinc-500 flex-shrink-0 flex items-center gap-1">
          <Clock size={12} />
          {formatDate(folder.created_at)}
        </div>
        <div className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity text-zinc-500">
          <ChevronRight size={14} />
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
      {/* Folder icon area */}
      <div className="relative h-32 flex items-center justify-center bg-amber-500/5">
        <FolderOpen size={48} className="text-amber-400/60 group-hover:text-amber-400 transition-colors" />
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
          <p className="text-sm text-white truncate group-hover:text-amber-300 transition-colors font-medium">
            {folder.name}
          </p>
        )}
        <div className="flex items-center justify-between mt-2 text-xs text-zinc-500">
          {childCount != null ? (
            <span>{t('resources.itemCount', { count: childCount })}</span>
          ) : (
            <span />
          )}
          <span>{formatDate(folder.created_at)}</span>
        </div>
      </div>
    </div>
  );
};
