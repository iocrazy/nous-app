import React, { useState, useRef, useEffect } from 'react';
import { X, File, Film, Image, FileText, Plus } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Resource, Tag } from '../types';
import { getResourceCoverUrl } from '../services/resourceService';

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
    <div className="flex justify-between items-center py-1.5">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className="text-xs text-zinc-300 text-right">{value}</span>
    </div>
  );
};

const TagsSection: React.FC<{
  assignedTags: Array<{ tag: Tag }>;
  allTags: Tag[];
  assignedTagIds: Set<string>;
  onAddTag: (tagId: string) => void;
  onRemoveTag: (tagId: string) => void;
}> = ({ assignedTags, allTags, assignedTagIds, onAddTag, onRemoveTag }) => {
  const { t } = useTranslation();
  const [showDropdown, setShowDropdown] = useState(false);
  const [search, setSearch] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
        setSearch('');
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const availableTags = allTags.filter((tag) => !assignedTagIds.has(tag.id));
  const filtered = search
    ? availableTags.filter((tag) => tag.name.toLowerCase().includes(search.toLowerCase()))
    : availableTags;

  return (
    <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3">
      <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
        {t('resources.tags')}
      </h4>
      <div className="flex flex-wrap gap-1.5">
        {assignedTags.map((item) => item.tag && (
          <span
            key={item.tag.id}
            className="group inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full transition-opacity"
            style={{
              backgroundColor: (item.tag.color || '#6366f1') + '20',
              color: item.tag.color || '#6366f1',
            }}
          >
            {item.tag.name}
            <button
              onClick={() => onRemoveTag(item.tag.id)}
              className="opacity-0 group-hover:opacity-100 transition-opacity hover:text-white"
              title="Remove"
            >
              <X size={10} />
            </button>
          </span>
        ))}
        {/* Add tag button + dropdown */}
        <div className="relative" ref={dropdownRef}>
          <button
            onClick={() => { setShowDropdown(!showDropdown); setSearch(''); }}
            className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-zinc-800 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700 transition-colors"
          >
            <Plus size={10} />
            {t('resources.addTag')}
          </button>
          {showDropdown && (
            <div className="absolute left-0 top-full mt-1 z-30 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl w-48 py-1">
              <div className="px-2 pb-1">
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search tags..."
                  className="w-full bg-zinc-800 border border-zinc-700/50 rounded px-2 py-1 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50"
                  autoFocus
                />
              </div>
              <div className="max-h-32 overflow-y-auto">
                {filtered.map((tag) => (
                  <button
                    key={tag.id}
                    onClick={() => { onAddTag(tag.id); setShowDropdown(false); setSearch(''); }}
                    className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors"
                  >
                    <span
                      className="w-2.5 h-2.5 rounded-full shrink-0"
                      style={{ backgroundColor: tag.color || '#6366f1' }}
                    />
                    <span className="truncate">{tag.name}</span>
                  </button>
                ))}
                {filtered.length === 0 && (
                  <p className="text-xs text-zinc-600 text-center py-2">{t('resources.noTagsAvailable')}</p>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
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

  // Thumbnail source: always use cover API endpoint (thumbnail_path is a server-local path)
  const thumbnailSrc = React.useMemo(() => {
    if ((resource.thumbnail_path || resource.cover_image_path) && resource.id) {
      return getResourceCoverUrl(String(resource.id));
    }
    return null;
  }, [resource.thumbnail_path, resource.cover_image_path, resource.id]);

  return (
    <div className="flex-1 min-w-0 h-full bg-zinc-900 overflow-y-auto">
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

      {/* Preview — show real thumbnail or fallback icon */}
      <div className={`mx-4 mt-4 h-44 rounded-xl overflow-hidden flex items-center justify-center ${bg}`}>
        {thumbnailSrc ? (
          <img
            src={thumbnailSrc}
            alt={resource.filename}
            className="w-full h-full object-cover"
            loading="lazy"
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
          />
        ) : (
          <IconComponent size={48} className={`${color} opacity-70`} />
        )}
      </div>

      {/* Filename */}
      <div className="px-4 mt-4">
        <h4 className="text-sm font-medium text-white break-words leading-snug">{resource.filename}</h4>
      </div>

      {/* File Properties */}
      <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3">
        <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
          {t('resources.fileProperties')}
        </h4>
        <div className="space-y-0">
          <InfoRow label={t('resources.fileType')} value={resource.file_type || resource.mime_type} />
          <InfoRow label={t('resources.size')} value={formatFileSize(resource.file_size_bytes)} />
          <InfoRow label={t('resources.createdAt')} value={formatDate(resource.created_at)} />
          {isVideo && (
            <>
              <InfoRow label={t('resources.duration')} value={formatDuration(resource.duration_seconds)} />
              <InfoRow label={t('resources.resolution')} value={resource.resolution} />
            </>
          )}
          {resource.current_version > 1 && (
            <InfoRow label={t('resources.version')} value={`v${resource.current_version}`} />
          )}
        </div>
      </div>

      {/* Source */}
      {resource.source_type && (
        <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3">
          <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
            {t('resources.source')}
          </h4>
          <span className="inline-flex items-center px-2 py-0.5 text-[11px] font-medium rounded-md bg-zinc-800 text-zinc-300">
            {resource.source_type === 'web' ? t('resources.webDownload') : t('resources.uploaded')}
          </span>
        </div>
      )}

      {/* Tags — matching detail page style */}
      <TagsSection
        assignedTags={assignedTags}
        allTags={allTags}
        assignedTagIds={assignedTagIds}
        onAddTag={onAddTag}
        onRemoveTag={onRemoveTag}
      />

      {/* Bottom spacing */}
      <div className="h-6" />
    </div>
  );
};
