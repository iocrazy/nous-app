import React from 'react';
import { Video, FileText, Image, File, Clock } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ProjectFile, ReviewStatus } from '../types';

interface FileCardProps {
  file: ProjectFile;
  onClick: () => void;
  viewMode: 'grid' | 'list';
  isSelected?: boolean;
  onToggleSelect?: (e: React.MouseEvent) => void;
}

const getFileIcon = (fileType: string | null) => {
  if (!fileType) return { icon: File, color: 'text-ink-400', bg: 'bg-ink-500/20' };
  const ft = fileType.toLowerCase();
  if (ft === 'video' || ft.startsWith('video')) return { icon: Video, color: 'text-purple-400', bg: 'bg-purple-500/20' };
  if (ft === 'image' || ft.startsWith('image')) return { icon: Image, color: 'text-green-400', bg: 'bg-green-500/20' };
  if (ft === 'document' || ft === 'pdf' || ft === 'text') return { icon: FileText, color: 'text-blue-400', bg: 'bg-blue-500/20' };
  return { icon: File, color: 'text-ink-400', bg: 'bg-ink-500/20' };
};

const formatFileSize = (bytes: number | null): string => {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
};

const formatDate = (dateStr: string): string => {
  return new Date(dateStr).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
};

const STATUS_STYLES: Record<ReviewStatus, { bg: string; text: string; label: string }> = {
  pending_review: { bg: 'bg-yellow-400/10', text: 'text-yellow-300', label: 'mediatrack.review.pendingReview' },
  in_review: { bg: 'bg-blue-400/10', text: 'text-blue-300', label: 'mediatrack.review.inReview' },
  feedback_collected: { bg: 'bg-orange-400/10', text: 'text-orange-300', label: 'mediatrack.review.feedbackCollected' },
  approved: { bg: 'bg-green-400/10', text: 'text-green-300', label: 'mediatrack.review.approved' },
};

const StatusBadge: React.FC<{ status: ReviewStatus }> = ({ status }) => {
  const { t } = useTranslation();
  const style = STATUS_STYLES[status];
  if (!style) return null;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium ${style.bg} ${style.text}`}>
      {t(style.label)}
    </span>
  );
};

export const FileCard: React.FC<FileCardProps> = ({ file, onClick, viewMode, isSelected, onToggleSelect }) => {
  const { icon: IconComponent, color, bg } = getFileIcon(file.file_type);

  const handleClick = (e: React.MouseEvent) => {
    if (e.shiftKey || e.metaKey || e.ctrlKey) {
      onToggleSelect?.(e);
    } else {
      onClick();
    }
  };

  if (viewMode === 'list') {
    return (
      <div
        onClick={handleClick}
        className={`flex items-center gap-4 px-4 py-3 bg-ink-800/60 hover:bg-ink-800 border rounded-xl cursor-pointer transition-all duration-200 group ${
          isSelected ? 'border-indigo-500 bg-indigo-500/10' : 'border-ink-700/30 hover:border-ink-600'
        }`}
      >
        {onToggleSelect && (
          <div
            onClick={(e) => { e.stopPropagation(); onToggleSelect(e); }}
            className={`w-5 h-5 rounded border-2 flex-shrink-0 flex items-center justify-center cursor-pointer transition-colors ${
              isSelected
                ? 'bg-indigo-500 border-indigo-500 text-white'
                : 'border-ink-600 hover:border-ink-400'
            }`}
          >
            {isSelected && <span className="text-[10px] font-bold">✓</span>}
          </div>
        )}
        <div className={`p-2 rounded-lg ${bg} flex-shrink-0`}>
          <IconComponent size={18} className={color} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm text-white truncate group-hover:text-indigo-300 transition-colors">
            {file.filename}
          </p>
        </div>
        {file.review_status && (
          <div className="flex-shrink-0">
            <StatusBadge status={file.review_status} />
          </div>
        )}
        <div className="text-xs text-ink-500 flex-shrink-0">
          {formatFileSize(file.file_size_bytes)}
        </div>
        <div className="text-xs text-ink-500 flex-shrink-0 flex items-center gap-1">
          <Clock size={12} />
          {formatDate(file.created_at)}
        </div>
      </div>
    );
  }

  return (
    <div
      onClick={handleClick}
      className={`bg-ink-800/80 hover:bg-ink-800 border rounded-xl cursor-pointer transition-all duration-200 group overflow-hidden relative ${
        isSelected ? 'border-indigo-500 ring-1 ring-indigo-500/50' : 'border-ink-700/50 hover:border-ink-600'
      }`}
    >
      {/* Selection checkbox */}
      {onToggleSelect && (
        <div
          onClick={(e) => { e.stopPropagation(); onToggleSelect(e); }}
          className={`absolute top-2 left-2 z-10 w-5 h-5 rounded border-2 flex items-center justify-center cursor-pointer transition-all ${
            isSelected
              ? 'bg-indigo-500 border-indigo-500 text-white opacity-100'
              : 'border-ink-500 opacity-0 group-hover:opacity-100 hover:border-ink-300'
          }`}
        >
          {isSelected && <span className="text-[10px] font-bold">✓</span>}
        </div>
      )}
      {/* Thumbnail area */}
      <div className={`h-32 flex items-center justify-center ${bg}`}>
        <IconComponent size={40} className={`${color} opacity-60 group-hover:opacity-100 transition-opacity`} />
      </div>
      {/* Info */}
      <div className="p-3">
        <p className="text-sm text-white truncate group-hover:text-indigo-300 transition-colors font-medium">
          {file.filename}
        </p>
        <div className="flex items-center justify-between mt-2 text-xs text-ink-500">
          <span>{formatFileSize(file.file_size_bytes)}</span>
          {file.review_status ? (
            <StatusBadge status={file.review_status} />
          ) : (
            <span>{formatDate(file.created_at)}</span>
          )}
        </div>
      </div>
    </div>
  );
};
