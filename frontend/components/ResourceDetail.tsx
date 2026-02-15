import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  Download,
  File,
  Film,
  Image,
  FileText,
  Music,
  Loader2,
  MoreHorizontal,
  Clock,
  HardDrive,
  Tag as TagIcon,
  X,
  Layers,
  FileQuestion,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Resource, ResourceVersion, Tag } from '../types';
import {
  fetchResourceById,
  fetchResourceVersions,
  fetchResourceTags,
  addResourceTag,
  removeResourceTag,
  getResourceFileUrl,
} from '../services/resourceService';
import { fetchTags } from '../services/tagsService';
import { getSupabaseAccessToken } from '../supabaseClient';

// ─── Utility functions ──────────────────────────────────

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
  if (mins >= 60) {
    const hrs = Math.floor(mins / 60);
    const remainMins = mins % 60;
    return `${hrs}:${remainMins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function getFileIcon(mimeType: string | null | undefined) {
  if (!mimeType) return { icon: File, color: 'text-zinc-400', bg: 'bg-zinc-500/20' };
  if (mimeType.startsWith('video/')) return { icon: Film, color: 'text-purple-400', bg: 'bg-purple-500/20' };
  if (mimeType.startsWith('image/')) return { icon: Image, color: 'text-green-400', bg: 'bg-green-500/20' };
  if (mimeType.startsWith('audio/')) return { icon: Music, color: 'text-orange-400', bg: 'bg-orange-500/20' };
  if (mimeType.startsWith('text/') || mimeType.includes('pdf') || mimeType.includes('document'))
    return { icon: FileText, color: 'text-blue-400', bg: 'bg-blue-500/20' };
  return { icon: File, color: 'text-zinc-400', bg: 'bg-zinc-500/20' };
}

// ─── FilePreview ─────────────────────────────────────────

const FilePreview: React.FC<{
  resource: Resource;
  fileUrl: string | null;
}> = ({ resource, fileUrl }) => {
  const { t } = useTranslation();
  const mime = resource.mime_type || '';

  if (!fileUrl) {
    const { icon: IconComponent, color } = getFileIcon(mime);
    return (
      <div className="flex flex-col items-center gap-4 text-center">
        <IconComponent size={64} className={`${color} opacity-60`} />
        <p className="text-zinc-400 text-sm">{resource.filename}</p>
        <p className="text-zinc-500 text-xs">{t('resources.noPreview')}</p>
      </div>
    );
  }

  if (mime.startsWith('video/')) {
    return (
      <video
        src={fileUrl}
        controls
        className="max-w-full max-h-full rounded-lg"
        poster={resource.thumbnail_path || undefined}
      />
    );
  }

  if (mime.startsWith('audio/')) {
    return (
      <div className="flex flex-col items-center gap-6">
        <div className="w-32 h-32 rounded-full bg-orange-500/10 flex items-center justify-center">
          <Music size={48} className="text-orange-400" />
        </div>
        <p className="text-zinc-300 text-sm font-medium">{resource.filename}</p>
        <audio src={fileUrl} controls className="w-80" />
      </div>
    );
  }

  if (mime.startsWith('image/')) {
    return (
      <img
        src={fileUrl}
        alt={resource.filename}
        className="max-w-full max-h-full object-contain rounded-lg"
      />
    );
  }

  if (mime === 'application/pdf') {
    return (
      <iframe
        src={fileUrl}
        className="w-full h-full rounded-lg"
        title={resource.filename}
      />
    );
  }

  // Default: icon + download
  const { icon: IconComponent, color } = getFileIcon(mime);
  return (
    <div className="flex flex-col items-center gap-4 text-center">
      <IconComponent size={64} className={`${color} opacity-60`} />
      <p className="text-zinc-300 font-medium">{resource.filename}</p>
      <p className="text-zinc-500 text-sm">{t('resources.noPreview')}</p>
      <a
        href={fileUrl}
        download
        className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm transition-colors"
      >
        <Download size={16} />
        {t('resources.download')}
      </a>
    </div>
  );
};

// ─── InfoRow ─────────────────────────────────────────────

const InfoRow: React.FC<{
  icon: React.ReactNode;
  label: string;
  value: string | null | undefined;
}> = ({ icon, label, value }) => {
  if (!value) return null;
  return (
    <div className="flex items-start gap-3 py-2">
      <span className="mt-0.5 text-zinc-500 shrink-0">{icon}</span>
      <div className="min-w-0">
        <p className="text-xs text-zinc-500">{label}</p>
        <p className="text-sm text-zinc-300 break-words">{value}</p>
      </div>
    </div>
  );
};

// ─── Main Component ──────────────────────────────────────

interface ResourceDetailProps {
  resourceId: string;
}

export const ResourceDetail: React.FC<ResourceDetailProps> = ({ resourceId }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { teamId } = useParams();

  // State
  const [resource, setResource] = useState<Resource | null>(null);
  const [versions, setVersions] = useState<ResourceVersion[]>([]);
  const [assignedTags, setAssignedTags] = useState<Array<{ tag: Tag }>>([]);
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [showMoreMenu, setShowMoreMenu] = useState(false);

  // Build authenticated file URL
  useEffect(() => {
    let cancelled = false;
    const buildUrl = async () => {
      try {
        const token = await getSupabaseAccessToken();
        if (!cancelled) {
          setFileUrl(getResourceFileUrl(resourceId, token || undefined));
        }
      } catch {
        if (!cancelled) {
          setFileUrl(getResourceFileUrl(resourceId));
        }
      }
    };
    buildUrl();
    return () => { cancelled = true; };
  }, [resourceId]);

  // Fetch resource data
  useEffect(() => {
    let cancelled = false;
    const loadData = async () => {
      setLoading(true);
      setError(null);
      try {
        const [res, vers, tags, allTagsList] = await Promise.all([
          fetchResourceById(resourceId),
          fetchResourceVersions(resourceId).catch(() => []),
          fetchResourceTags(resourceId).catch(() => []),
          fetchTags().catch(() => []),
        ]);
        if (!cancelled) {
          setResource(res);
          setVersions(vers);
          setAssignedTags(tags);
          setAllTags(allTagsList);
        }
      } catch (err) {
        if (!cancelled) {
          setError('Failed to load resource');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };
    loadData();
    return () => { cancelled = true; };
  }, [resourceId]);

  // Tag handlers
  const handleAddTag = useCallback(async (tagId: string) => {
    try {
      await addResourceTag(resourceId, tagId);
      const updated = await fetchResourceTags(resourceId);
      setAssignedTags(updated);
    } catch { /* ignore */ }
  }, [resourceId]);

  const handleRemoveTag = useCallback(async (tagId: string) => {
    try {
      await removeResourceTag(resourceId, tagId);
      setAssignedTags((prev) => prev.filter((t) => t.tag?.id !== tagId));
    } catch { /* ignore */ }
  }, [resourceId]);

  const handleBack = useCallback(() => {
    navigate(-1);
  }, [navigate]);

  // Loading state
  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-full min-h-[400px]">
        <Loader2 size={32} className="animate-spin text-zinc-500 mb-3" />
        <p className="text-zinc-500 text-sm">{t('common.loading')}</p>
      </div>
    );
  }

  // Error state
  if (error || !resource) {
    return (
      <div className="flex flex-col items-center justify-center h-full min-h-[400px] text-center">
        <FileQuestion size={48} className="text-zinc-600 mb-4" />
        <p className="text-zinc-400 text-lg font-medium mb-2">{error || 'Resource not found'}</p>
        <button
          onClick={handleBack}
          className="flex items-center gap-2 px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors mt-4"
        >
          <ArrowLeft size={16} />
          {t('common.back')}
        </button>
      </div>
    );
  }

  const { icon: FileIcon, color: iconColor, bg: iconBg } = getFileIcon(resource.mime_type);
  const isVideo = resource.mime_type?.startsWith('video/');
  const isAudio = resource.mime_type?.startsWith('audio/');
  const assignedTagIds = new Set(assignedTags.map((t) => t.tag?.id).filter(Boolean));

  return (
    <div className="flex flex-col h-full animate-in fade-in duration-300">
      {/* Top bar */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-zinc-800 shrink-0">
        <button
          onClick={handleBack}
          className="flex items-center gap-2 text-sm text-zinc-400 hover:text-zinc-200 transition-colors"
        >
          <ArrowLeft size={16} />
          <span>{t('common.back')}</span>
        </button>
        <div className="flex items-center gap-2">
          {/* Download button */}
          {fileUrl && (
            <a
              href={fileUrl}
              download
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
            >
              <Download size={14} />
              <span>{t('resources.download')}</span>
            </a>
          )}
          {/* More menu */}
          <div className="relative">
            <button
              onClick={() => setShowMoreMenu(!showMoreMenu)}
              className="p-1.5 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 rounded-lg transition-colors"
            >
              <MoreHorizontal size={16} />
            </button>
            {showMoreMenu && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setShowMoreMenu(false)} />
                <div className="absolute right-0 top-full mt-1 z-20 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl py-1 w-44">
                  {fileUrl && (
                    <a
                      href={fileUrl}
                      download
                      className="block w-full text-left px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors"
                      onClick={() => setShowMoreMenu(false)}
                    >
                      {t('resources.downloadOriginal')}
                    </a>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: Preview area */}
        <div className="flex-1 flex items-center justify-center bg-zinc-950 p-6 min-w-0">
          <FilePreview resource={resource} fileUrl={fileUrl} />
        </div>

        {/* Right: Info panel */}
        <div className="w-80 border-l border-zinc-800 overflow-y-auto p-4 space-y-5 shrink-0">
          {/* File icon + name */}
          <div className="flex items-start gap-3">
            <div className={`p-2.5 rounded-xl ${iconBg} shrink-0`}>
              <FileIcon size={20} className={iconColor} />
            </div>
            <div className="min-w-0">
              <h2 className="text-base font-medium text-white break-words leading-snug">
                {resource.filename}
              </h2>
              <p className="text-xs text-zinc-500 mt-0.5">
                {resource.file_type || resource.mime_type || 'Unknown type'}
              </p>
            </div>
          </div>

          {/* Metadata section */}
          <div className="space-y-0">
            <InfoRow
              icon={<HardDrive size={14} />}
              label={t('resources.size')}
              value={formatFileSize(resource.file_size_bytes)}
            />
            <InfoRow
              icon={<Clock size={14} />}
              label={t('resources.createdAt')}
              value={formatDate(resource.created_at)}
            />
            {(isVideo || isAudio) && resource.duration_seconds != null && (
              <InfoRow
                icon={<Film size={14} />}
                label={t('resources.duration')}
                value={formatDuration(resource.duration_seconds)}
              />
            )}
            {isVideo && resource.resolution && (
              <InfoRow
                icon={<Layers size={14} />}
                label={t('resources.resolution')}
                value={resource.resolution}
              />
            )}
          </div>

          {/* Tags section */}
          <div>
            <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-2">
              {t('resources.tags')}
            </h4>
            {/* Assigned tags */}
            {assignedTags.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-3">
                {assignedTags.map((item) => item.tag && (
                  <span
                    key={item.tag.id}
                    className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full cursor-pointer hover:opacity-80 transition-opacity"
                    style={{
                      backgroundColor: (item.tag.color || '#6366f1') + '20',
                      color: item.tag.color || '#6366f1',
                    }}
                    onClick={() => handleRemoveTag(item.tag.id)}
                    title="Click to remove"
                  >
                    {item.tag.name}
                    <X size={10} />
                  </span>
                ))}
              </div>
            )}
            {/* Available tags */}
            <div className="space-y-0.5 max-h-32 overflow-y-auto">
              {allTags.filter((tag) => !assignedTagIds.has(tag.id)).map((tag) => (
                <button
                  key={tag.id}
                  onClick={() => handleAddTag(tag.id)}
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

          {/* Versions section */}
          {versions.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-2">
                {t('resources.versions')}
              </h4>
              <div className="space-y-2">
                {versions
                  .sort((a, b) => b.version_number - a.version_number)
                  .map((ver) => (
                    <div
                      key={ver.id}
                      className={`flex items-center justify-between px-3 py-2 rounded-lg text-xs ${
                        ver.version_number === resource.current_version
                          ? 'bg-indigo-500/10 border border-indigo-500/30'
                          : 'bg-zinc-800/50'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span className={`font-medium ${
                          ver.version_number === resource.current_version
                            ? 'text-indigo-400'
                            : 'text-zinc-300'
                        }`}>
                          v{ver.version_number}
                        </span>
                        {ver.version_number === resource.current_version && (
                          <span className="text-indigo-400/70 text-[10px]">
                            current
                          </span>
                        )}
                      </div>
                      <span className="text-zinc-500">
                        {formatDate(ver.created_at)}
                      </span>
                    </div>
                  ))}
              </div>
            </div>
          )}

          {/* Source info */}
          <div>
            <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-2">
              Source
            </h4>
            <p className="text-xs text-zinc-500">
              {resource.source_type === 'web' ? 'Downloaded from web' : 'Uploaded'}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
