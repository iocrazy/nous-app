import React, { useState, useRef, useEffect, useCallback } from 'react';
import { X, FolderOpen, Pencil } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Folder } from '../types';
import { getResourceCoverUrl } from '../services/resourceService';

interface FolderPreviewItem {
  resource_id?: string | null;
  thumbnail_path?: string | null;
  cover_image_path?: string | null;
  mime_type?: string | null;
}

interface FolderInfoPanelProps {
  folder: Folder;
  previewItems?: FolderPreviewItem[];
  readOnly?: boolean;
  onClose: () => void;
  onRename: (name: string) => void;
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return 'Unknown';
  return new Date(dateStr).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

const InfoRow = ({ label, value }: { label: string; value?: string | null }) => {
  if (!value) return null;
  return (
    <div className="flex justify-between items-center py-1.5">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className="text-xs text-zinc-300 text-right">{value}</span>
    </div>
  );
};

export const FolderInfoPanel: React.FC<FolderInfoPanelProps> = ({
  folder,
  previewItems,
  readOnly = false,
  onClose,
  onRename,
}) => {
  const { t } = useTranslation();

  // Editable name
  const [editingName, setEditingName] = useState(false);
  const [nameValue, setNameValue] = useState(folder.name);
  const nameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setNameValue(folder.name);
    setEditingName(false);
  }, [folder.id, folder.name]);

  useEffect(() => {
    if (editingName) nameInputRef.current?.select();
  }, [editingName]);

  const commitName = useCallback(() => {
    setEditingName(false);
    const trimmed = nameValue.trim();
    if (trimmed && trimmed !== folder.name) {
      onRename(trimmed);
    } else {
      setNameValue(folder.name);
    }
  }, [nameValue, folder.name, onRename]);

  const folderColor = folder.color || '#71717a';
  const hasPreview = previewItems && previewItems.length > 0;
  const previewSlots = previewItems ? previewItems.slice(0, 4) : [];

  return (
    <div className="flex-1 min-w-0 h-full bg-zinc-900 overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-zinc-800 sticky top-0 bg-zinc-900 z-10">
        <h3 className="text-sm font-semibold text-white">{t('resources.folderInfoPanel.title')}</h3>
        <button
          onClick={onClose}
          className="p-1.5 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
        >
          <X size={16} />
        </button>
      </div>

      {/* Preview — 4-grid thumbnails or folder icon fallback */}
      <div className={`mx-4 mt-4 h-44 rounded-xl overflow-hidden bg-zinc-800/50 ${hasPreview ? '' : 'flex items-center justify-center'}`}>
        {hasPreview ? (
          <div className="w-full h-full grid grid-cols-2 grid-rows-2 gap-px">
            {[0, 1, 2, 3].map((idx) => {
              const item = previewSlots[idx];
              if (!item) return <div key={idx} className="bg-zinc-800" />;
              const src = (item.thumbnail_path || item.cover_image_path) && item.resource_id
                ? getResourceCoverUrl(String(item.resource_id))
                : null;
              return src ? (
                <img
                  key={idx}
                  src={src}
                  alt=""
                  className="w-full h-full object-cover"
                  loading="lazy"
                  onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                />
              ) : (
                <div key={idx} className="bg-zinc-800 flex items-center justify-center">
                  <FolderOpen size={16} className="text-zinc-600" />
                </div>
              );
            })}
          </div>
        ) : (
          <div
            className="w-20 h-20 rounded-2xl flex items-center justify-center"
            style={{ backgroundColor: `${folderColor}20` }}
          >
            <FolderOpen size={40} style={{ color: folderColor }} />
          </div>
        )}
      </div>

      {/* Editable Folder Name */}
      <div className="px-4 mt-4">
        {!readOnly && editingName ? (
          <input
            ref={nameInputRef}
            value={nameValue}
            onChange={(e) => setNameValue(e.target.value)}
            onBlur={commitName}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitName();
              if (e.key === 'Escape') { setNameValue(folder.name); setEditingName(false); }
            }}
            className="w-full bg-zinc-800 border border-indigo-500/50 rounded px-2 py-1 text-sm text-white focus:outline-none"
            autoFocus
          />
        ) : readOnly ? (
          <h4 className="text-sm font-medium text-white break-words leading-snug">{folder.name}</h4>
        ) : (
          <div
            className="group flex items-start gap-1.5 cursor-pointer"
            onClick={() => setEditingName(true)}
          >
            <h4 className="text-sm font-medium text-white break-words leading-snug flex-1">{folder.name}</h4>
            <Pencil size={12} className="text-zinc-600 group-hover:text-zinc-400 mt-0.5 shrink-0 transition-colors" />
          </div>
        )}
      </div>

      {/* Properties */}
      <div className="px-4 mt-6 border-t border-zinc-800/60 pt-3">
        <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
          {t('resources.infoPanel.properties')}
        </h4>
        <div className="space-y-0">
          {folder.resource_count != null && (
            <InfoRow
              label={t('resources.folderInfoPanel.itemCount', 'Items')}
              value={String(folder.resource_count)}
            />
          )}
          <InfoRow
            label={t('resources.infoPanel.type')}
            value={t('resources.folderInfoPanel.typeFolder', 'Folder')}
          />
          <InfoRow
            label={t('resources.infoPanel.created')}
            value={formatDate(folder.created_at)}
          />
          <InfoRow
            label={t('resources.infoPanel.modified')}
            value={formatDate(folder.updated_at)}
          />
        </div>
      </div>

      {/* Bottom spacing */}
      <div className="h-6" />
    </div>
  );
};
