import React, { useState, useRef, useEffect, useCallback } from 'react';
import { X, File, Film, Image, FileText, Plus, Pencil, FolderOpen, Star, Music } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Resource, Tag } from '../types';
import { getResourceCoverUrl } from '../services/resourceService';

interface ResourceInfoPanelProps {
  resource: Resource;
  allTags: Tag[];
  assignedTags: Array<{ tag: Tag }>;
  folderName?: string | null;
  onClose: () => void;
  onAddTag: (tagId: string) => void;
  onRemoveTag: (tagId: string) => void;
  onUpdate: (data: Partial<Resource>) => void;
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
  if (mimeType.startsWith('audio/')) return { icon: Music, color: 'text-amber-400', bg: 'bg-amber-500/20' };
  if (mimeType.startsWith('text/') || mimeType.includes('pdf') || mimeType.includes('document'))
    return { icon: FileText, color: 'text-blue-400', bg: 'bg-blue-500/20' };
  return { icon: File, color: 'text-zinc-400', bg: 'bg-zinc-500/20' };
}

// ─── Star Rating ─────────────────────────────────────────

const StarRating: React.FC<{ value: number; onChange: (v: number) => void }> = ({ value, onChange }) => {
  const [hover, setHover] = useState(0);

  return (
    <div className="flex items-center gap-0.5" onMouseLeave={() => setHover(0)}>
      {[1, 2, 3, 4, 5].map((star) => (
        <button
          key={star}
          className="p-0 transition-colors"
          onMouseEnter={() => setHover(star)}
          onClick={() => onChange(star === value ? 0 : star)}
        >
          <Star
            size={14}
            className={
              (hover || value) >= star
                ? 'text-amber-400 fill-amber-400'
                : 'text-zinc-600'
            }
          />
        </button>
      ))}
    </div>
  );
};

// ─── Info Row ────────────────────────────────────────────

const InfoRow = ({ label, value, children }: { label: string; value?: string | null | undefined; children?: React.ReactNode }) => {
  if (!value && !children) return null;
  return (
    <div className="flex justify-between items-center py-1.5">
      <span className="text-xs text-zinc-500">{label}</span>
      {children || <span className="text-xs text-zinc-300 text-right">{value}</span>}
    </div>
  );
};

// ─── Tags Section ────────────────────────────────────────

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

// ─── Main Component ──────────────────────────────────────

export const ResourceInfoPanel: React.FC<ResourceInfoPanelProps> = ({
  resource,
  allTags,
  assignedTags,
  folderName,
  onClose,
  onAddTag,
  onRemoveTag,
  onUpdate,
}) => {
  const { t } = useTranslation();
  const { icon: IconComponent, color, bg } = getFileIcon(resource.mime_type);
  const assignedTagIds = new Set(assignedTags.map((t) => t.tag?.id).filter(Boolean));

  const isMedia = resource.mime_type?.startsWith('video/') || resource.mime_type?.startsWith('audio/');

  // ─── Editable filename ──────────────────────────────
  const [editingName, setEditingName] = useState(false);
  const [nameValue, setNameValue] = useState(resource.filename);
  const nameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setNameValue(resource.filename);
    setEditingName(false);
  }, [resource.id, resource.filename]);

  useEffect(() => {
    if (editingName) nameInputRef.current?.select();
  }, [editingName]);

  const commitName = useCallback(() => {
    setEditingName(false);
    const trimmed = nameValue.trim();
    if (trimmed && trimmed !== resource.filename) {
      onUpdate({ filename: trimmed });
    } else {
      setNameValue(resource.filename);
    }
  }, [nameValue, resource.filename, onUpdate]);

  // ─── Notes ──────────────────────────────────────────
  const [notesValue, setNotesValue] = useState(resource.notes || '');
  const notesTimerRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    setNotesValue(resource.notes || '');
  }, [resource.id, resource.notes]);

  const commitNotes = useCallback(() => {
    clearTimeout(notesTimerRef.current);
    const val = notesValue.trim();
    if (val !== (resource.notes || '').trim()) {
      onUpdate({ notes: val || null } as Partial<Resource>);
    }
  }, [notesValue, resource.notes, onUpdate]);

  // ─── URL ────────────────────────────────────────────
  const [urlValue, setUrlValue] = useState(resource.url || '');

  useEffect(() => {
    setUrlValue(resource.url || '');
  }, [resource.id, resource.url]);

  const commitUrl = useCallback(() => {
    const val = urlValue.trim();
    if (val !== (resource.url || '').trim()) {
      onUpdate({ url: val || null } as Partial<Resource>);
    }
  }, [urlValue, resource.url, onUpdate]);

  // ─── Rating ─────────────────────────────────────────
  const handleRating = useCallback((v: number) => {
    onUpdate({ rating: v });
  }, [onUpdate]);

  // ─── Thumbnail ──────────────────────────────────────
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
        <h3 className="text-sm font-semibold text-white">{t('resources.infoPanel.title')}</h3>
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

      {/* Editable Filename */}
      <div className="px-4 mt-4">
        {editingName ? (
          <input
            ref={nameInputRef}
            value={nameValue}
            onChange={(e) => setNameValue(e.target.value)}
            onBlur={commitName}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitName();
              if (e.key === 'Escape') { setNameValue(resource.filename); setEditingName(false); }
            }}
            className="w-full bg-zinc-800 border border-indigo-500/50 rounded px-2 py-1 text-sm text-white focus:outline-none"
            autoFocus
          />
        ) : (
          <div
            className="group flex items-start gap-1.5 cursor-pointer"
            onClick={() => setEditingName(true)}
          >
            <h4 className="text-sm font-medium text-white break-words leading-snug flex-1">{resource.filename}</h4>
            <Pencil size={12} className="text-zinc-600 group-hover:text-zinc-400 mt-0.5 shrink-0 transition-colors" />
          </div>
        )}
      </div>

      {/* Notes */}
      <div className="px-4 mt-3">
        <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-1.5">
          {t('resources.infoPanel.notes')}
        </h4>
        <textarea
          value={notesValue}
          onChange={(e) => setNotesValue(e.target.value)}
          onBlur={commitNotes}
          placeholder={t('resources.infoPanel.notesPlaceholder')}
          rows={3}
          className="w-full bg-zinc-800/50 border border-zinc-700/50 rounded-lg px-2.5 py-2 text-xs text-zinc-300 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50 resize-none"
        />
      </div>

      {/* URL */}
      <div className="px-4 mt-2">
        <input
          value={urlValue}
          onChange={(e) => setUrlValue(e.target.value)}
          onBlur={commitUrl}
          onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
          placeholder={t('resources.infoPanel.urlPlaceholder')}
          className="w-full bg-zinc-800/50 border border-zinc-700/50 rounded-lg px-2.5 py-1.5 text-xs text-zinc-300 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50"
        />
      </div>

      {/* Tags */}
      <TagsSection
        assignedTags={assignedTags}
        allTags={allTags}
        assignedTagIds={assignedTagIds}
        onAddTag={onAddTag}
        onRemoveTag={onRemoveTag}
      />

      {/* Folders */}
      {folderName && (
        <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3">
          <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
            {t('resources.infoPanel.folders')}
          </h4>
          <div className="flex items-center gap-1.5 text-xs text-zinc-300">
            <FolderOpen size={13} className="text-zinc-500 shrink-0" />
            <span className="truncate">{folderName}</span>
          </div>
        </div>
      )}

      {/* Properties */}
      <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3">
        <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
          {t('resources.infoPanel.properties')}
        </h4>
        <div className="space-y-0">
          <InfoRow label={t('resources.infoPanel.rating')}>
            <StarRating value={resource.rating ?? 0} onChange={handleRating} />
          </InfoRow>
          {isMedia && resource.duration_seconds && (
            <InfoRow label={t('resources.infoPanel.duration')} value={formatDuration(resource.duration_seconds)} />
          )}
          <InfoRow label={t('resources.infoPanel.size')} value={formatFileSize(resource.file_size_bytes)} />
          <InfoRow label={t('resources.infoPanel.type')} value={resource.file_type || resource.mime_type} />
          {resource.resolution && (
            <InfoRow label={t('resources.infoPanel.resolution')} value={resource.resolution} />
          )}
          {resource.current_version > 1 && (
            <InfoRow label={t('resources.infoPanel.version')} value={`v${resource.current_version}`} />
          )}
          <InfoRow
            label={t('resources.infoPanel.source')}
            value={resource.source_type === 'web' ? t('resources.infoPanel.sourceWeb') : t('resources.infoPanel.sourceUpload')}
          />
          <InfoRow label={t('resources.infoPanel.created')} value={formatDate(resource.created_at)} />
          <InfoRow label={t('resources.infoPanel.modified')} value={formatDate(resource.updated_at)} />
        </div>
      </div>

      {/* Bottom spacing */}
      <div className="h-6" />
    </div>
  );
};
