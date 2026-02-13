import React from 'react';
import { X, File, Film, Image, FileText } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Resource, Tag } from '../types';

interface ResourceInfoPanelProps {
  resource: Resource;
  allTags: Tag[];
  assignedTags: Array<{ tag: Tag }>;
  onClose: () => void;
  onAddTag: (tagId: string) => void;
  onRemoveTag: (tagId: string) => void;
}

function formatFileSize(bytes: number | null | undefined): string {
  if (!bytes) return 'Unknown';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
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

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds) return '';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function getFileIcon(mimeType: string | null | undefined) {
  if (!mimeType) return { icon: File, color: 'text-zinc-400', bg: 'bg-zinc-500/20' };
  if (mimeType.startsWith('video/')) return { icon: Film, color: 'text-purple-400', bg: 'bg-purple-500/20' };
  if (mimeType.startsWith('image/')) return { icon: Image, color: 'text-green-400', bg: 'bg-green-500/20' };
  if (mimeType.startsWith('text/') || mimeType.includes('pdf') || mimeType.includes('document'))
    return { icon: FileText, color: 'text-blue-400', bg: 'bg-blue-500/20' };
  return { icon: File, color: 'text-zinc-400', bg: 'bg-zinc-500/20' };
}

const InfoRow = ({ label, value }: { label: string; value: string | null | undefined }) => {
  if (!value) return null;
  return (
    <div className="flex justify-between items-center py-2 border-b border-zinc-800/50">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className="text-sm text-zinc-300">{value}</span>
    </div>
  );
};

export const ResourceInfoPanel: React.FC<ResourceInfoPanelProps> = ({
  resource,
  allTags,
  assignedTags,
  onClose,
  onAddTag,
  onRemoveTag,
}) => {
  const { t } = useTranslation();
  const { icon: IconComponent, color, bg } = getFileIcon(resource.mime_type);
  const assignedTagIds = new Set(assignedTags.map((t) => t.tag?.id).filter(Boolean));

  const isVideo = resource.mime_type?.startsWith('video/');

  return (
    <div className="w-80 bg-zinc-900 border-l border-zinc-800 h-full overflow-y-auto animate-in slide-in-from-right-4 duration-300 shrink-0">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-zinc-800 sticky top-0 bg-zinc-900 z-10">
        <h3 className="text-sm font-semibold text-white">{t('resources.resourceInfo')}</h3>
        <button
          onClick={onClose}
          className="p-1.5 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
        >
          <X size={16} />
        </button>
      </div>

      {/* Preview */}
      <div className={`mx-4 mt-4 h-40 rounded-xl flex items-center justify-center ${bg}`}>
        <IconComponent size={48} className={`${color} opacity-70`} />
      </div>

      {/* Basic Info */}
      <div className="px-4 mt-4">
        <h4 className="text-sm font-medium text-white mb-1 break-words">{resource.filename}</h4>
        <div className="space-y-0">
          <InfoRow label={t('resources.fileType')} value={resource.file_type || resource.mime_type} />
          <InfoRow label={t('resources.size')} value={formatFileSize(resource.file_size_bytes)} />
          <InfoRow label={t('resources.createdAt')} value={formatDate(resource.created_at)} />
        </div>
      </div>

      {/* Media Info (conditional) */}
      {isVideo && (resource.duration_seconds || resource.resolution) && (
        <div className="px-4 mt-5">
          <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-2">
            {t('resources.mediaInfo')}
          </h4>
          <div className="space-y-0">
            <InfoRow label={t('resources.duration')} value={formatDuration(resource.duration_seconds)} />
            <InfoRow label={t('resources.resolution')} value={resource.resolution} />
          </div>
        </div>
      )}

      {/* Version */}
      {resource.current_version > 1 && (
        <div className="px-4 mt-5">
          <InfoRow label={t('resources.version')} value={`v${resource.current_version}`} />
        </div>
      )}

      {/* Tags */}
      <div className="px-4 mt-5">
        <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-2">
          {t('resources.tags')}
        </h4>
        {/* Assigned tags */}
        {assignedTags.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-3">
            {assignedTags.map((t) => t.tag && (
              <span
                key={t.tag.id}
                className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full cursor-pointer hover:opacity-80 transition-opacity"
                style={{
                  backgroundColor: (t.tag.color || '#6366f1') + '20',
                  color: t.tag.color || '#6366f1',
                }}
                onClick={() => onRemoveTag(t.tag.id)}
                title="Click to remove"
              >
                {t.tag.name}
                <X size={10} />
              </span>
            ))}
          </div>
        )}
        {/* Available tags to add */}
        <div className="space-y-0.5 max-h-40 overflow-y-auto">
          {allTags.filter((tag) => !assignedTagIds.has(tag.id)).map((tag) => (
            <button
              key={tag.id}
              onClick={() => onAddTag(tag.id)}
              className="w-full flex items-center gap-2 px-2 py-1.5 text-xs rounded-lg text-zinc-400 hover:bg-zinc-800 transition-colors"
            >
              <span
                className="w-2 h-2 rounded-full shrink-0"
                style={{ backgroundColor: tag.color || '#6366f1' }}
              />
              <span className="truncate">{tag.name}</span>
            </button>
          ))}
          {allTags.length === 0 && (
            <p className="text-xs text-zinc-600 py-1">{t('resources.noTagsAvailable')}</p>
          )}
        </div>
      </div>

      {/* Bottom spacing */}
      <div className="h-6" />
    </div>
  );
};
