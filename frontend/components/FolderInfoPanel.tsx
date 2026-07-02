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
      <span className="text-xs text-content-3">{label}</span>
      <span className="text-xs text-content-2 text-right">{value}</span>
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

  // Island redesign: neutral text/border aligned to the mock --content/--line ladder.
  const cPrimary = 'text-content';
  const cText400 = 'text-content-2';
  const cLabel = 'text-content-3';
  const cFaint = 'text-content-4';
  const cHoverPrimary = 'hover:text-content';
  const cGroupHover400 = 'group-hover:text-content-2';
  const cBorderHeader = 'border-line';
  const cBorderSection = 'border-line';
  // Surface tokens — mirror the resource/download panels: preview/placeholder/
  // input/hover surfaces painted with --island-2.
  const cHoverSurface = 'hover:bg-island-2';
  const cPreviewBg = 'bg-island-2';
  const cTileBg = 'bg-island-2';
  const cFieldBg = 'bg-island-2';

  return (
    <div className="flex-1 min-w-0 h-full overflow-y-auto">
      {/* Header */}
      <div className={`flex items-center justify-between p-4 border-b ${cBorderHeader} sticky top-0 z-10 bg-island`}>
        <h3 className={`text-sm font-semibold ${cPrimary}`}>{t('resources.folderInfoPanel.title')}</h3>
        <button
          onClick={onClose}
          className={`p-1.5 ${cText400} ${cHoverPrimary} ${cHoverSurface} rounded-lg transition-colors`}
        >
          <X size={16} />
        </button>
      </div>

      {/* Preview — 4-grid thumbnails or folder icon fallback */}
      <div className={`mx-4 mt-4 h-44 rounded-xl overflow-hidden ${cPreviewBg} ${hasPreview ? '' : 'flex items-center justify-center'}`}>
        {hasPreview ? (
          <div className="w-full h-full grid grid-cols-2 grid-rows-2 gap-px">
            {[0, 1, 2, 3].map((idx) => {
              const item = previewSlots[idx];
              if (!item) return <div key={idx} className={cTileBg} />;
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
                <div key={idx} className={`${cTileBg} flex items-center justify-center`}>
                  <FolderOpen size={16} className={cFaint} />
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
            className={`w-full ${cFieldBg} border border-indigo-500/50 rounded px-2 py-1 text-sm ${cPrimary} focus:outline-none`}
            autoFocus
          />
        ) : readOnly ? (
          <h4 className={`text-sm font-medium ${cPrimary} break-words leading-snug`}>{folder.name}</h4>
        ) : (
          <div
            className="group flex items-start gap-1.5 cursor-pointer"
            onClick={() => setEditingName(true)}
          >
            <h4 className={`text-sm font-medium ${cPrimary} break-words leading-snug flex-1`}>{folder.name}</h4>
            <Pencil size={12} className={`${cFaint} ${cGroupHover400} mt-0.5 shrink-0 transition-colors`} />
          </div>
        )}
      </div>

      {/* Properties */}
      <div className={`px-4 mt-6 border-t ${cBorderSection} pt-3`}>
        <h4 className={`text-[11px] font-semibold ${cLabel} uppercase tracking-widest mb-2`}>
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
