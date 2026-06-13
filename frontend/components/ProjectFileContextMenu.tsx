import React, { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Download, Pencil, FolderInput, ClipboardCheck,
  History, Info, Share2, Trash2,
} from 'lucide-react';
import { ProjectFile, ReviewStatus } from '../types';

interface ProjectFileContextMenuProps {
  file: ProjectFile;
  x: number;
  y: number;
  onClose: () => void;
  onDownload: (file: ProjectFile) => void;
  onRename: (file: ProjectFile) => void;
  onMove: (file: ProjectFile) => void;
  onSetStatus: (file: ProjectFile, status: ReviewStatus | null) => void;
  onVersionHistory: (file: ProjectFile) => void;
  onFileInfo: (file: ProjectFile) => void;
  onShare: (file: ProjectFile) => void;
  onDelete: (file: ProjectFile) => void;
}

const STATUSES: { value: ReviewStatus | null; labelKey: string }[] = [
  { value: null, labelKey: 'projects.fileMenu.statusNone' },
  { value: 'pending_review', labelKey: 'projects.fileMenu.statusPending' },
  { value: 'in_review', labelKey: 'projects.fileMenu.statusInReview' },
  { value: 'feedback_collected', labelKey: 'projects.fileMenu.statusFeedback' },
  { value: 'approved', labelKey: 'projects.fileMenu.statusApproved' },
];

export const ProjectFileContextMenu: React.FC<ProjectFileContextMenuProps> = ({
  file, x, y, onClose, onDownload, onRename, onMove,
  onSetStatus, onVersionHistory, onFileInfo, onShare, onDelete,
}) => {
  const { t } = useTranslation();
  const menuRef = useRef<HTMLDivElement>(null);
  const [showStatusSub, setShowStatusSub] = React.useState(false);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  // Adjust position to stay within viewport
  const style: React.CSSProperties = {
    position: 'fixed',
    left: x,
    top: y,
    zIndex: 100,
  };

  const MenuItem: React.FC<{
    icon: React.ReactNode;
    label: string;
    onClick: () => void;
    danger?: boolean;
    onMouseEnter?: () => void;
  }> = ({ icon, label, onClick, danger, onMouseEnter }) => (
    <button
      onClick={() => { onClick(); onClose(); }}
      onMouseEnter={onMouseEnter}
      className={`w-full flex items-center gap-2.5 px-3 py-2 text-sm rounded-lg transition-colors ${
        danger
          ? 'text-red-400 hover:bg-red-500/10'
          : 'text-ink-300 hover:bg-ink-700/50'
      }`}
    >
      {icon}
      <span>{label}</span>
    </button>
  );

  return (
    <div ref={menuRef} style={style}
      className="bg-ink-900 border border-ink-700 rounded-xl shadow-2xl py-1.5 w-52 overflow-hidden"
    >
      <MenuItem
        icon={<Download size={14} />}
        label={t('projects.fileMenu.download', 'Download')}
        onClick={() => onDownload(file)}
        onMouseEnter={() => setShowStatusSub(false)}
      />
      <MenuItem
        icon={<Pencil size={14} />}
        label={t('projects.fileMenu.rename', 'Rename')}
        onClick={() => onRename(file)}
        onMouseEnter={() => setShowStatusSub(false)}
      />
      <MenuItem
        icon={<FolderInput size={14} />}
        label={t('projects.fileMenu.move', 'Move To...')}
        onClick={() => onMove(file)}
        onMouseEnter={() => setShowStatusSub(false)}
      />

      <div className="border-t border-ink-800 my-1" />

      {/* Status submenu */}
      <div className="relative"
        onMouseEnter={() => setShowStatusSub(true)}
        onMouseLeave={() => setShowStatusSub(false)}
      >
        <div className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-ink-300 hover:bg-ink-700/50 rounded-lg cursor-default">
          <ClipboardCheck size={14} />
          <span className="flex-1">{t('projects.fileMenu.setStatus', 'Set Status')}</span>
          <span className="text-ink-600 text-xs">›</span>
        </div>
        {showStatusSub && (
          <div className="absolute left-full top-0 ml-1 bg-ink-900 border border-ink-700 rounded-xl shadow-2xl py-1.5 w-44">
            {STATUSES.map((s) => (
              <button
                key={s.value ?? 'none'}
                onClick={() => { onSetStatus(file, s.value); onClose(); }}
                className={`w-full flex items-center gap-2 px-3 py-2 text-sm rounded-lg transition-colors ${
                  file.review_status === s.value
                    ? 'text-indigo-300 bg-indigo-500/10'
                    : 'text-ink-300 hover:bg-ink-700/50'
                }`}
              >
                {t(s.labelKey)}
              </button>
            ))}
          </div>
        )}
      </div>

      <MenuItem
        icon={<History size={14} />}
        label={t('projects.fileMenu.versionHistory', 'Version History')}
        onClick={() => onVersionHistory(file)}
        onMouseEnter={() => setShowStatusSub(false)}
      />
      <MenuItem
        icon={<Info size={14} />}
        label={t('projects.fileMenu.fileInfo', 'File Info')}
        onClick={() => onFileInfo(file)}
        onMouseEnter={() => setShowStatusSub(false)}
      />
      <MenuItem
        icon={<Share2 size={14} />}
        label={t('projects.fileMenu.share', 'Share')}
        onClick={() => onShare(file)}
        onMouseEnter={() => setShowStatusSub(false)}
      />

      <div className="border-t border-ink-800 my-1" />

      <MenuItem
        icon={<Trash2 size={14} />}
        label={t('projects.fileMenu.delete', 'Delete')}
        onClick={() => onDelete(file)}
        danger
        onMouseEnter={() => setShowStatusSub(false)}
      />
    </div>
  );
};
