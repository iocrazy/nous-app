import React, { useState, useRef, useEffect, useCallback } from 'react';
import { X, File, Film, Image, FileText, Pencil, FolderOpen, Star, Music, Brain, Sparkles, Eye, Loader2, Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Resource, Tag } from '../types';
import { getResourceCoverUrl } from '../services/resourceService';
import { UnifiedTagPicker } from './UnifiedTagPicker';

interface ResourceInfoPanelProps {
  resource: Resource;
  allTags: Tag[];
  assignedTags: Tag[];
  folderName?: string | null;
  readOnly?: boolean;
  onClose: () => void;
  onAddTag: (tagId: string) => void;
  onRemoveTag: (tagId: string) => void;
  onCreate?: (name: string, color: string) => Promise<Tag | null>;
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

// ─── AI Status Badge ────────────────────────────────────

const AIStatusBadge: React.FC<{ status?: string }> = ({ status }) => {
  switch (status) {
    case 'processing':
      return (
        <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-400">
          <Loader2 size={9} className="animate-spin" /> Processing
        </span>
      );
    case 'completed':
      return (
        <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400">
          <Check size={9} /> Done
        </span>
      );
    case 'failed':
      return (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-400">
          Failed
        </span>
      );
    case 'pending':
      return (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-500/10 text-zinc-500">
          Pending
        </span>
      );
    default:
      return null;
  }
};

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

// ─── Main Component ──────────────────────────────────────

export const ResourceInfoPanel: React.FC<ResourceInfoPanelProps> = ({
  resource,
  allTags,
  assignedTags,
  folderName,
  readOnly = false,
  onClose,
  onAddTag,
  onRemoveTag,
  onCreate,
  onUpdate,
}) => {
  const { t } = useTranslation();
  const { icon: IconComponent, color, bg } = getFileIcon(resource.mime_type);

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
        <h3 className="text-sm font-semibold text-white select-none">{t('resources.infoPanel.title')}</h3>
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
        {!readOnly && editingName ? (
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
        ) : readOnly ? (
          <h4 className="text-sm font-medium text-white break-words leading-snug">{resource.filename}</h4>
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
      {!readOnly && (
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
      )}
      {readOnly && notesValue && (
        <div className="px-4 mt-3">
          <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-1.5">
            {t('resources.infoPanel.notes')}
          </h4>
          <p className="text-xs text-zinc-400 whitespace-pre-wrap">{notesValue}</p>
        </div>
      )}

      {/* URL */}
      {!readOnly && (
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
      )}
      {readOnly && urlValue && (
        <div className="px-4 mt-2">
          <a href={urlValue} target="_blank" rel="noopener noreferrer" className="text-xs text-indigo-400 hover:underline break-all">{urlValue}</a>
        </div>
      )}

      {/* Tags */}
      <UnifiedTagPicker
        assignedTags={assignedTags}
        allTags={allTags}
        readOnly={readOnly}
        onAdd={onAddTag}
        onRemove={onRemoveTag}
        onCreate={onCreate}
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

      {/* AI Status */}
      {(resource.transcript_status && resource.transcript_status !== 'none') ||
       (resource.summary_status && resource.summary_status !== 'none') ||
       (resource.visual_analysis_status && resource.visual_analysis_status !== 'none') ? (
        <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3">
          <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
            {t('resources.infoPanel.aiStatus')}
          </h4>
          <div className="space-y-0">
            {resource.transcript_status && resource.transcript_status !== 'none' && (
              <div className="flex items-center justify-between py-1">
                <div className="flex items-center gap-2">
                  <Brain size={12} className="text-cyan-400" />
                  <span className="text-xs text-zinc-400">Transcript</span>
                </div>
                <AIStatusBadge status={resource.transcript_status} />
              </div>
            )}
            {resource.summary_status && resource.summary_status !== 'none' && (
              <div className="flex items-center justify-between py-1">
                <div className="flex items-center gap-2">
                  <Sparkles size={12} className="text-indigo-400" />
                  <span className="text-xs text-zinc-400">Summary</span>
                </div>
                <AIStatusBadge status={resource.summary_status} />
              </div>
            )}
            {resource.visual_analysis_status && resource.visual_analysis_status !== 'none' && (
              <div className="flex items-center justify-between py-1">
                <div className="flex items-center gap-2">
                  <Eye size={12} className="text-purple-400" />
                  <span className="text-xs text-zinc-400">Visual Analysis</span>
                </div>
                <AIStatusBadge status={resource.visual_analysis_status} />
              </div>
            )}
          </div>
        </div>
      ) : null}

      {/* Properties */}
      <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3">
        <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
          {t('resources.infoPanel.properties')}
        </h4>
        <div className="space-y-0">
          <InfoRow label={t('resources.infoPanel.rating')}>
            <StarRating value={resource.rating ?? 0} onChange={readOnly ? () => {} : handleRating} />
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
