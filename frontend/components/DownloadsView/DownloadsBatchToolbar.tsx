// frontend/components/DownloadsView/DownloadsBatchToolbar.tsx
//
// Mobile bottom batch-action bar for the Downloads grid (Pixcall-style). Shown
// when one or more items are selected. Downloads supports: Download / Tag /
// Share / Delete (no Move — that's an Upload/Resources action). Share is
// per-resource, so it's enabled only when exactly one item is selected.

import { createPortal } from 'react-dom';
import { Download, Tag as TagIcon, Share2, Trash2, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface DownloadsBatchToolbarProps {
  count: number;
  onDownload: () => void;
  onTag: () => void;
  onShare: () => void;
  onDelete: () => void;
  onCancel: () => void;
}

export function DownloadsBatchToolbar({
  count,
  onDownload,
  onTag,
  onShare,
  onDelete,
  onCancel,
}: DownloadsBatchToolbarProps) {
  const { t } = useTranslation();
  if (count === 0) return null;

  const shareEnabled = count === 1;

  const Action = ({
    icon,
    label,
    onClick,
    disabled,
    danger,
  }: {
    icon: React.ReactNode;
    label: string;
    onClick: () => void;
    disabled?: boolean;
    danger?: boolean;
  }) => (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`flex flex-col items-center gap-1 px-3 py-1 rounded-lg active:bg-ink-800 disabled:opacity-35 ${
        danger ? 'text-red-400' : 'text-ink-200'
      }`}
    >
      {icon}
      <span className="text-[10px]">{label}</span>
    </button>
  );

  return createPortal(
    <div
      className="md:hidden fixed bottom-0 inset-x-0 z-50 bg-ink-900/95 backdrop-blur border-t border-ink-800 animate-in slide-in-from-bottom duration-200"
      style={{ paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 8px)' }}
    >
      <div className="flex items-center justify-between px-3 pt-2">
        <span className="text-xs text-ink-400 pl-1">
          {t('resources.selected', { count })}
        </span>
        <button
          type="button"
          onClick={onCancel}
          aria-label={t('common.cancel', 'Cancel')}
          className="w-8 h-8 rounded-full flex items-center justify-center text-ink-400 active:bg-ink-800"
        >
          <X size={18} />
        </button>
      </div>
      <div className="flex items-stretch justify-around px-2 pb-1">
        <Action
          icon={<Download size={20} />}
          label={t('common.download', 'Download')}
          onClick={onDownload}
        />
        <Action
          icon={<TagIcon size={20} />}
          label={t('resources.filter.tags', 'Tags')}
          onClick={onTag}
        />
        <Action
          icon={<Share2 size={20} />}
          label={t('common.share', 'Share')}
          onClick={onShare}
          disabled={!shareEnabled}
        />
        <Action
          icon={<Trash2 size={20} />}
          label={t('common.delete', 'Delete')}
          onClick={onDelete}
          danger
        />
      </div>
    </div>,
    document.body,
  );
}
