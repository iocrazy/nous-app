import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  File,
  Film,
  Image,
  FileText,
  Music,
  Loader2,
  MoreHorizontal,
  X,
  Layers,
  FileQuestion,
  PanelLeft,
  Plus,
  RefreshCw,
  Search,
  Share2,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Resource, ResourceItem, ResourceVersion, Tag } from '../types';
import {
  fetchResourceById,
  fetchResourceVersions,
  fetchResourceTags,
  addResourceTag,
  removeResourceTag,
  getResourceFileUrl,
  getVersionFileUrl,
  getVersionHlsUrl,
  fetchResourceContext,
  fetchResources,
  getResourceCoverUrl,
  retryTranscode,
} from '../services/resourceService';
import { fetchTags } from '../services/tagsService';
import { getSupabaseAccessToken, getSupabaseClient } from '../supabaseClient';
import { formatDateLocalized } from '../utils/formatDate';
import { ShareModal } from './ShareModal';
import { VersionManagerModal } from './VersionManagerModal';
import VideoPlayer from './VideoPlayer';
import { KeyboardShortcutsDialog } from './KeyboardShortcutsDialog';
import { ResourceReviewPanel } from './ResourceReviewPanel';
import { ResourceAnnotationOverlay, NormalizedAnnotation } from './ResourceAnnotationOverlay';
import { AudioWaveformPlayer } from './AudioWaveformPlayer';
import { fetchComments } from '../services/reviewService';

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
  return formatDateLocalized(dateStr);
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

function getFileExtension(filename: string | null | undefined): string {
  if (!filename) return '';
  const parts = filename.split('.');
  return parts.length > 1 ? parts[parts.length - 1].toUpperCase() : '';
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
        className="max-w-full max-h-[calc(100vh-13rem)] rounded-lg object-contain"
        poster={resource.thumbnail_path || undefined}
      />
    );
  }

  if (mime.startsWith('audio/')) {
    return (
      <AudioWaveformPlayer
        src={fileUrl}
        filename={resource.filename}
        duration={resource.duration_seconds ?? undefined}
      />
    );
  }

  if (mime.startsWith('image/')) {
    return (
      <img
        src={fileUrl}
        alt={resource.filename}
        className="max-w-full max-h-[calc(100vh-13rem)] object-contain rounded-lg"
      />
    );
  }

  if (mime === 'application/pdf') {
    return (
      <iframe
        src={fileUrl}
        className="w-full h-[calc(100vh-13rem)] rounded-lg"
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

// ─── CompactInfoRow ─────────────────────────────────────

const CompactInfoRow: React.FC<{
  label: string;
  value: string | null | undefined;
}> = ({ label, value }) => {
  if (!value) return null;
  return (
    <div className="flex justify-between items-center py-1.5">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className="text-xs text-zinc-300 text-right">{value}</span>
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
  const [showShareModal, setShowShareModal] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [showVersionDropdown, setShowVersionDropdown] = useState(false);
  const [showVersionManager, setShowVersionManager] = useState(false);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [authToken, setAuthToken] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const versionDropdownRef = useRef<HTMLDivElement>(null);

  // File list panel state
  const [showFileList, setShowFileList] = useState(false);
  const [siblingFiles, setSiblingFiles] = useState<ResourceItem[]>([]);
  const [fileListSearch, setFileListSearch] = useState('');

  // Navigation state
  const [currentIndex, setCurrentIndex] = useState<number>(-1);

  // Inspector state
  const [notes, setNotes] = useState('');
  const [showTagDropdown, setShowTagDropdown] = useState(false);
  const [tagSearch, setTagSearch] = useState('');
  const tagDropdownRef = useRef<HTMLDivElement>(null);

  // Review state
  const [rightTab, setRightTab] = useState<'info' | 'review'>('info');
  const [currentUserId, setCurrentUserId] = useState<string>('');
  const [videoCurrentTime, setVideoCurrentTime] = useState(0);
  const [annotationActive, setAnnotationActive] = useState(false);
  const [pendingAnnotations, setPendingAnnotations] = useState<NormalizedAnnotation[]>([]);
  const [viewAnnotations, setViewAnnotations] = useState<NormalizedAnnotation[] | undefined>();
  const [commentMarkers, setCommentMarkers] = useState<Array<{ time: number }>>([]);

  // Load sibling files for file list panel
  useEffect(() => {
    let cancelled = false;
    const loadSiblings = async () => {
      try {
        const ctx = await fetchResourceContext(resourceId);
        if (!ctx || cancelled) return;
        const items = await fetchResources(
          ctx.scope_type as 'personal' | 'team',
          ctx.scope_id,
          ctx.folder_id,
          ctx.library_id,
        );
        if (!cancelled) setSiblingFiles(items);
      } catch {
        if (!cancelled) setSiblingFiles([]);
      }
    };
    loadSiblings();
    return () => { cancelled = true; };
  }, [resourceId]);

  // Compute current index in sibling list
  useEffect(() => {
    const idx = siblingFiles.findIndex(item => item.resource?.id === resourceId);
    setCurrentIndex(idx);
  }, [siblingFiles, resourceId]);

  // Navigation functions
  const navigateToSibling = useCallback((dir: 'prev' | 'next') => {
    const newIdx = dir === 'prev' ? currentIndex - 1 : currentIndex + 1;
    if (newIdx < 0 || newIdx >= siblingFiles.length) return;
    const target = siblingFiles[newIdx];
    if (target.resource?.id) {
      navigate(`${teamId ? `/t/${teamId}` : ''}/resources/file/${target.resource.id}`);
    }
  }, [siblingFiles, currentIndex, teamId, navigate]);

  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex >= 0 && currentIndex < siblingFiles.length - 1;

  // Keyboard ← → navigation (disabled for video — VideoPlayer uses arrows for seeking)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (resource?.mime_type?.startsWith('video/')) return;
      if (e.key === 'ArrowLeft') { e.preventDefault(); navigateToSibling('prev'); }
      if (e.key === 'ArrowRight') { e.preventDefault(); navigateToSibling('next'); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigateToSibling, resource]);

  // Reset notes when file changes
  useEffect(() => { setNotes(''); }, [resourceId]);

  // Close tag dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (tagDropdownRef.current && !tagDropdownRef.current.contains(e.target as Node)) {
        setShowTagDropdown(false);
        setTagSearch('');
      }
      if (versionDropdownRef.current && !versionDropdownRef.current.contains(e.target as Node)) {
        setShowVersionDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const filteredSiblings = fileListSearch
    ? siblingFiles.filter((item) =>
        (item.resource?.filename ?? '').toLowerCase().includes(fileListSearch.toLowerCase())
      )
    : siblingFiles;

  // Build authenticated file URL (for current resource or selected version)
  // Prefer HLS URL when transcode is completed for the viewing version
  useEffect(() => {
    let cancelled = false;
    const buildUrl = async () => {
      try {
        const token = await getSupabaseAccessToken();
        if (cancelled) return;
        setAuthToken(token);

        // Determine which version we're looking at
        const viewingVersion = selectedVersionId
          ? versions.find((v) => v.id === selectedVersionId)
          : versions.find((v) => v.version_number === resource?.current_version);

        // If the version has HLS ready, use HLS URL for video
        if (
          viewingVersion?.hls_path &&
          viewingVersion.transcode_status === 'completed' &&
          resource?.mime_type?.startsWith('video/')
        ) {
          setFileUrl(getVersionHlsUrl(resourceId, viewingVersion.id, token || undefined));
        } else if (selectedVersionId) {
          setFileUrl(getVersionFileUrl(resourceId, selectedVersionId, token || undefined));
        } else {
          setFileUrl(getResourceFileUrl(resourceId, token || undefined));
        }
      } catch {
        if (cancelled) return;
        if (selectedVersionId) {
          setFileUrl(getVersionFileUrl(resourceId, selectedVersionId));
        } else {
          setFileUrl(getResourceFileUrl(resourceId));
        }
      }
    };
    buildUrl();
    return () => { cancelled = true; };
  }, [resourceId, selectedVersionId, versions, resource?.current_version, resource?.mime_type]);

  // Handle version change from VersionManagerModal (re-fetch resource + versions)
  const handleVersionChange = useCallback(async () => {
    try {
      const [res, vers] = await Promise.all([
        fetchResourceById(resourceId),
        fetchResourceVersions(resourceId).catch(() => []),
      ]);
      setResource(res);
      setVersions(vers);
      setSelectedVersionId(null);
    } catch { /* ignore */ }
  }, [resourceId]);

  // Select a specific version to preview
  const handleSelectVersion = useCallback((version: ResourceVersion) => {
    if (version.version_number === resource?.current_version) {
      setSelectedVersionId(null);
    } else {
      setSelectedVersionId(version.id);
    }
    setShowVersionDropdown(false);
  }, [resource]);

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
      setShowTagDropdown(false);
      setTagSearch('');
    } catch { /* ignore */ }
  }, [resourceId]);

  const handleRemoveTag = useCallback(async (tagId: string) => {
    try {
      await removeResourceTag(resourceId, tagId);
      setAssignedTags((prev) => prev.filter((t) => t.tag?.id !== tagId));
    } catch { /* ignore */ }
  }, [resourceId]);

  // Get current user ID for review panel
  useEffect(() => {
    const loadUser = async () => {
      const client = getSupabaseClient();
      if (!client) return;
      try {
        const { data: { user } } = await client.auth.getUser();
        if (user) setCurrentUserId(user.id);
      } catch { /* ignore */ }
    };
    loadUser();
  }, []);

  // Load comment markers for video timeline
  const refreshCommentMarkers = useCallback(async () => {
    try {
      const cmts = await fetchComments(resourceId);
      setCommentMarkers(
        cmts.filter((c) => c.timecode != null).map((c) => ({ time: c.timecode! })),
      );
    } catch { /* ignore */ }
  }, [resourceId]);

  useEffect(() => {
    if (resource?.mime_type?.startsWith('video/')) {
      refreshCommentMarkers();
    }
  }, [resource?.mime_type, refreshCommentMarkers]);

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
  const viewingVersion = selectedVersionId
    ? versions.find((v) => v.id === selectedVersionId)
    : versions.find((v) => v.version_number === resource.current_version);
  const transcodeStatus = viewingVersion?.transcode_status;
  const assignedTagIds = new Set(assignedTags.map((t) => t.tag?.id).filter(Boolean));
  const fileExt = getFileExtension(resource.filename);
  const availableTags = allTags.filter((tag) => !assignedTagIds.has(tag.id));
  const filteredAvailableTags = tagSearch
    ? availableTags.filter((tag) => tag.name.toLowerCase().includes(tagSearch.toLowerCase()))
    : availableTags;

  return (
    <div className="flex flex-col h-full animate-in fade-in duration-300">
      {/* Top bar — [PanelLeft | ← Back] | [◀ prev | filename (2/5) | next ▶] | [Download | ⋯] */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800 shrink-0">
        {/* Left: panel toggle + back */}
        <div className="flex items-center gap-1.5 min-w-[140px]">
          <button
            onClick={() => setShowFileList(!showFileList)}
            className={`p-1.5 rounded-lg transition-colors ${
              showFileList ? 'bg-zinc-800 text-indigo-400' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
            }`}
            title={t('resources.fileListPanel')}
          >
            <PanelLeft size={16} />
          </button>
          <button
            onClick={handleBack}
            className="flex items-center gap-1.5 text-sm text-zinc-400 hover:text-zinc-200 transition-colors"
          >
            <ArrowLeft size={16} />
            <span>{t('common.back')}</span>
          </button>
        </div>

        {/* Center: prev/next + filename */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => navigateToSibling('prev')}
            disabled={!hasPrev}
            className={`p-1 rounded transition-colors ${
              hasPrev ? 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800' : 'text-zinc-700 cursor-not-allowed'
            }`}
            title={t('resources.prevFile')}
          >
            <ChevronLeft size={18} />
          </button>
          <div className="flex items-center gap-2 px-2">
            <FileIcon size={14} className={iconColor} />
            <span className="text-sm text-zinc-200 font-medium max-w-[300px] truncate">
              {resource.filename}
            </span>
            {/* Version dropdown */}
            {versions.length > 0 && (
              <div className="relative" ref={versionDropdownRef}>
                <button
                  onClick={() => setShowVersionDropdown(!showVersionDropdown)}
                  className={`flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-md transition-colors ${
                    selectedVersionId
                      ? 'bg-amber-500/20 text-amber-300 hover:bg-amber-500/30'
                      : 'bg-zinc-800 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700'
                  }`}
                >
                  <Layers size={12} />
                  <span>
                    {selectedVersionId
                      ? `v${versions.find(v => v.id === selectedVersionId)?.version_number ?? '?'}`
                      : `v${resource.current_version}`}
                  </span>
                  <ChevronDown size={12} />
                </button>
                {showVersionDropdown && (
                  <div className="absolute left-1/2 -translate-x-1/2 top-full mt-1 z-30 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl w-56 py-1">
                    <div className="px-3 py-1.5 border-b border-zinc-800">
                      <p className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">
                        {t('resources.versions', 'Versions')}
                      </p>
                    </div>
                    <div className="max-h-48 overflow-y-auto py-1">
                      {versions
                        .sort((a, b) => b.version_number - a.version_number)
                        .map((ver) => {
                          const isCurrentVer = ver.version_number === resource.current_version;
                          const isSelected = selectedVersionId ? ver.id === selectedVersionId : isCurrentVer;
                          return (
                            <button
                              key={ver.id}
                              onClick={() => handleSelectVersion(ver)}
                              className={`w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors ${
                                isSelected
                                  ? 'bg-indigo-500/10 text-indigo-300'
                                  : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
                              }`}
                            >
                              <span className={`font-semibold ${isCurrentVer ? 'text-indigo-400' : ''}`}>
                                v{ver.version_number}
                              </span>
                              <span className="truncate flex-1 text-left">{ver.filename}</span>
                              {ver.transcode_status === 'completed' && (
                                <span className="text-[9px] px-1 py-0.5 bg-emerald-500/20 text-emerald-400 rounded">
                                  HLS
                                </span>
                              )}
                              {(ver.transcode_status === 'pending' || ver.transcode_status === 'processing') && (
                                <Loader2 size={10} className="animate-spin text-amber-400 shrink-0" />
                              )}
                              {isCurrentVer && (
                                <span className="text-[9px] px-1 py-0.5 bg-emerald-500/20 text-emerald-400 rounded">
                                  current
                                </span>
                              )}
                            </button>
                          );
                        })}
                    </div>
                    <div className="border-t border-zinc-800 px-2 py-1.5">
                      <button
                        onClick={() => { setShowVersionDropdown(false); setShowVersionManager(true); }}
                        className="w-full flex items-center gap-2 px-2 py-1.5 text-xs text-indigo-400 hover:bg-indigo-500/10 rounded-md transition-colors"
                      >
                        <Layers size={12} />
                        {t('resources.manageVersions', 'Manage Versions')}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
            {/* Transcoding status badge */}
            {isVideo && transcodeStatus === 'pending' && (
              <span className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium bg-amber-500/15 text-amber-400 rounded-md">
                <Loader2 size={10} className="animate-spin" />
                {t('resources.transcoding', 'Transcoding...')}
              </span>
            )}
            {isVideo && transcodeStatus === 'processing' && (
              <span className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium bg-amber-500/15 text-amber-400 rounded-md">
                <Loader2 size={10} className="animate-spin" />
                {t('resources.transcoding', 'Transcoding...')}
              </span>
            )}
            {isVideo && transcodeStatus === 'failed' && viewingVersion && (
              <button
                onClick={async () => {
                  try {
                    await retryTranscode(resourceId, viewingVersion.id);
                    // Optimistic update: show as pending
                    setVersions((prev) =>
                      prev.map((v) =>
                        v.id === viewingVersion.id
                          ? { ...v, transcode_status: 'pending' }
                          : v,
                      ),
                    );
                  } catch {
                    // silently fail — user sees it stays as "failed"
                  }
                }}
                className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 hover:text-red-300 rounded-md transition-colors cursor-pointer"
                title={t('resources.retryTranscode', 'Retry')}
              >
                <RefreshCw size={10} />
                {t('resources.transcodeFailed', 'Transcode Failed')}
              </button>
            )}
            {isVideo && transcodeStatus === 'completed' && (
              <span className="px-1.5 py-0.5 text-[10px] font-medium bg-emerald-500/15 text-emerald-400 rounded-md">
                HLS
              </span>
            )}
            {currentIndex >= 0 && siblingFiles.length > 0 && (
              <span className="text-xs text-zinc-500">
                ({currentIndex + 1}/{siblingFiles.length})
              </span>
            )}
          </div>
          <button
            onClick={() => navigateToSibling('next')}
            disabled={!hasNext}
            className={`p-1 rounded transition-colors ${
              hasNext ? 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800' : 'text-zinc-700 cursor-not-allowed'
            }`}
            title={t('resources.nextFile')}
          >
            <ChevronRight size={18} />
          </button>
        </div>

        {/* Right: download + more */}
        <div className="flex items-center gap-1.5 min-w-[140px] justify-end">
          <button
            onClick={() => setShowShareModal(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-purple-600 hover:bg-purple-500 text-white rounded-lg transition-colors"
          >
            <Share2 size={14} />
            <span>{t('resources.share')}</span>
          </button>
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
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* File list panel */}
        {showFileList && (
          <div className="w-64 border-r border-zinc-800/80 flex flex-col shrink-0">
            {/* Header + search */}
            <div className="px-3 py-2.5 border-b border-zinc-800/60">
              <p className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">{t('resources.fileListPanel')}</p>
              <div className="relative">
                <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-600" />
                <input
                  type="text"
                  value={fileListSearch}
                  onChange={(e) => setFileListSearch(e.target.value)}
                  placeholder={t('resources.searchFiles')}
                  className="w-full bg-zinc-800/60 border border-zinc-700/30 rounded-lg pl-8 pr-3 py-1.5 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50 transition-colors"
                />
              </div>
            </div>
            {/* File list */}
            <div className="flex-1 overflow-y-auto py-1">
              {filteredSiblings.map((item) => {
                const isActive = item.resource?.id === resourceId;
                const thumb = item.resource?.thumbnail_path || (item.resource?.cover_image_path && item.resource?.id ? getResourceCoverUrl(String(item.resource.id)) : null);
                const { icon: SibIcon, color: sibColor, bg: sibBg } = getFileIcon(item.resource?.mime_type);
                return (
                  <button
                    key={item.id}
                    onClick={() => {
                      if (item.resource?.id && item.resource.id !== resourceId) {
                        const basePath = teamId ? `/t/${teamId}` : '';
                        navigate(`${basePath}/resources/file/${item.resource.id}`);
                      }
                    }}
                    className={`w-full flex items-center gap-2.5 px-3 py-2 text-left transition-all ${
                      isActive
                        ? 'bg-indigo-500/10 border-l-2 border-indigo-400'
                        : 'hover:bg-zinc-800/60 border-l-2 border-transparent'
                    }`}
                  >
                    {thumb ? (
                      <img
                        src={thumb}
                        alt=""
                        className={`w-8 h-8 rounded object-cover shrink-0 ${isActive ? 'ring-1 ring-indigo-400/50' : ''}`}
                        onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                      />
                    ) : (
                      <div className={`w-8 h-8 rounded ${sibBg} flex items-center justify-center shrink-0`}>
                        <SibIcon size={14} className={sibColor} />
                      </div>
                    )}
                    <span className={`text-xs truncate ${isActive ? 'text-indigo-300 font-medium' : 'text-zinc-400'}`}>
                      {item.resource?.filename ?? 'Untitled'}
                    </span>
                  </button>
                );
              })}
              {filteredSiblings.length === 0 && (
                <p className="text-xs text-zinc-600 text-center py-6">{t('resources.noResources')}</p>
              )}
            </div>
          </div>
        )}

        {/* Preview area */}
        <div className="flex-1 flex flex-col bg-zinc-950 min-w-0 overflow-hidden">
          {/* Version preview banner */}
          {selectedVersionId && (
            <div className="flex items-center justify-center gap-2 px-3 py-1.5 bg-amber-500/10 border-b border-amber-500/20 text-xs text-amber-300 shrink-0">
              <Layers size={12} />
              <span>
                {t('resources.viewingVersion', 'Viewing version')} v{versions.find(v => v.id === selectedVersionId)?.version_number}
              </span>
              <button
                onClick={() => setSelectedVersionId(null)}
                className="ml-2 px-2 py-0.5 bg-amber-500/20 hover:bg-amber-500/30 rounded text-amber-200 transition-colors"
              >
                {t('resources.backToCurrent', 'Back to current')}
              </button>
            </div>
          )}
          <div className="flex-1 flex items-center justify-center min-h-0 min-w-0 overflow-hidden">
          {isVideo && fileUrl ? (
            <div className="w-full h-full relative">
              <VideoPlayer
                src={fileUrl}
                mimeType={resource.mime_type || undefined}
                authToken={authToken || undefined}
                playerRef={videoRef}
                onTimeUpdate={(t) => setVideoCurrentTime(t)}
                onDurationChange={() => {}}
                onToggleShortcuts={() => setShowShortcuts((s) => !s)}
                commentMarkers={commentMarkers}
              />
              <ResourceAnnotationOverlay
                isActive={annotationActive}
                viewAnnotations={viewAnnotations}
                onDone={(anns) => {
                  setPendingAnnotations(anns);
                  setAnnotationActive(false);
                }}
                onCancel={() => setAnnotationActive(false)}
              />
            </div>
          ) : isAudio && fileUrl ? (
            <div className="w-full h-full">
              <FilePreview resource={resource} fileUrl={fileUrl} />
            </div>
          ) : (
            <div className="p-6">
              <FilePreview resource={resource} fileUrl={fileUrl} />
            </div>
          )}
          </div>
        </div>

        {/* Right: Inspector panel (Eagle style) */}
        <div className="w-80 border-l border-zinc-800 flex flex-col shrink-0">
          {/* Tab bar */}
          <div className="flex border-b border-zinc-800 shrink-0">
            <button
              onClick={() => setRightTab('info')}
              className={`flex-1 px-3 py-2 text-xs font-medium transition-colors ${
                rightTab === 'info'
                  ? 'text-zinc-200 border-b-2 border-indigo-500'
                  : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              Info
            </button>
            <button
              onClick={() => { setRightTab('review'); setViewAnnotations(undefined); }}
              className={`flex-1 px-3 py-2 text-xs font-medium transition-colors ${
                rightTab === 'review'
                  ? 'text-zinc-200 border-b-2 border-indigo-500'
                  : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              Review
            </button>
          </div>

          {rightTab === 'info' ? (
          <div className="overflow-y-auto flex-1">
          {/* Section 1 — File Header */}
          <div className="px-4 py-4">
            <div className="flex items-start gap-3">
              <div className={`p-2 rounded-lg ${iconBg} shrink-0`}>
                <FileIcon size={18} className={iconColor} />
              </div>
              <div className="min-w-0 flex-1">
                <h2 className="text-sm font-medium text-white break-words leading-snug">
                  {resource.filename}
                </h2>
                <div className="flex items-center gap-2 mt-1">
                  {fileExt && (
                    <span className="px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide bg-zinc-800 text-zinc-400 rounded">
                      {fileExt}
                    </span>
                  )}
                  {resource.current_version != null && (
                    <span className="text-[10px] text-zinc-500">v{resource.current_version}</span>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Section 2 — File Properties */}
          <div className="px-4 py-3 border-t border-zinc-800">
            <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-1">
              {t('resources.fileProperties')}
            </h4>
            <CompactInfoRow label={t('resources.size')} value={formatFileSize(resource.file_size_bytes)} />
            <CompactInfoRow label={t('resources.createdAt')} value={formatDate(resource.created_at)} />
            {(isVideo || isAudio) && resource.duration_seconds != null && (
              <CompactInfoRow label={t('resources.duration')} value={formatDuration(resource.duration_seconds)} />
            )}
            {isVideo && resource.resolution && (
              <CompactInfoRow label={t('resources.resolution')} value={resource.resolution.replace(':', 'x')} />
            )}
            <CompactInfoRow label={t('resources.mimeType')} value={resource.mime_type} />
          </div>

          {/* Section 3 — Tags (Eagle style) */}
          <div className="px-4 py-3 border-t border-zinc-800">
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
                    onClick={() => handleRemoveTag(item.tag.id)}
                    className="opacity-0 group-hover:opacity-100 transition-opacity hover:text-white"
                    title="Remove"
                  >
                    <X size={10} />
                  </button>
                </span>
              ))}
              {/* Add tag button */}
              <div className="relative" ref={tagDropdownRef}>
                <button
                  onClick={() => { setShowTagDropdown(!showTagDropdown); setTagSearch(''); }}
                  className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-zinc-800 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700 transition-colors"
                >
                  <Plus size={10} />
                  {t('resources.addTag')}
                </button>
                {showTagDropdown && (
                  <div className="absolute left-0 top-full mt-1 z-30 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl w-48 py-1">
                    <div className="px-2 pb-1">
                      <input
                        type="text"
                        value={tagSearch}
                        onChange={(e) => setTagSearch(e.target.value)}
                        placeholder="Search tags..."
                        className="w-full bg-zinc-800 border border-zinc-700/50 rounded px-2 py-1 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50"
                        autoFocus
                      />
                    </div>
                    <div className="max-h-32 overflow-y-auto">
                      {filteredAvailableTags.map((tag) => (
                        <button
                          key={tag.id}
                          onClick={() => handleAddTag(tag.id)}
                          className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors"
                        >
                          <span
                            className="w-2.5 h-2.5 rounded-full shrink-0"
                            style={{ backgroundColor: tag.color || '#6366f1' }}
                          />
                          <span className="truncate">{tag.name}</span>
                        </button>
                      ))}
                      {filteredAvailableTags.length === 0 && (
                        <p className="text-xs text-zinc-600 text-center py-2">{t('resources.noTagsAvailable')}</p>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Section 4 — Notes */}
          <div className="px-4 py-3 border-t border-zinc-800">
            <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
              {t('resources.notes')}
            </h4>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder={t('resources.notesPlaceholder')}
              rows={3}
              className="w-full bg-zinc-800/50 border border-zinc-700/30 rounded-lg px-3 py-2 text-xs text-zinc-300 placeholder-zinc-600 resize-none focus:outline-none focus:border-indigo-500/50 transition-colors"
            />
          </div>

          {/* Section 5 — Source */}
          <div className="px-4 py-3 border-t border-zinc-800">
            <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
              {t('resources.source')}
            </h4>
            <div className="flex items-center gap-2">
              <span className={`px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide rounded ${
                resource.source_type === 'web'
                  ? 'bg-blue-500/10 text-blue-400'
                  : 'bg-emerald-500/10 text-emerald-400'
              }`}>
                {resource.source_type === 'web' ? t('resources.webDownload') : t('resources.uploaded')}
              </span>
            </div>
            {resource.media_id && (
              <a
                href={`/resources/media/${resource.media_id}`}
                className="inline-flex items-center gap-1.5 mt-2 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
                target="_blank"
                rel="noopener noreferrer"
              >
                <ExternalLink size={12} />
                {t('resources.viewOriginal')}
              </a>
            )}
          </div>

          {/* Section 6 — Versions */}
          {versions.length > 0 && (
            <div className="px-4 py-3 border-t border-zinc-800">
              <div className="flex items-center justify-between mb-2">
                <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest">
                  {t('resources.versions')}
                </h4>
                <button
                  onClick={() => setShowVersionManager(true)}
                  className="text-[10px] text-indigo-400 hover:text-indigo-300 transition-colors"
                >
                  {t('resources.manageVersions', 'Manage')}
                </button>
              </div>
              <div className="space-y-1">
                {versions
                  .sort((a, b) => b.version_number - a.version_number)
                  .map((ver) => {
                    const isCurrentVer = ver.version_number === resource.current_version;
                    const isViewing = selectedVersionId ? ver.id === selectedVersionId : isCurrentVer;
                    return (
                      <button
                        key={ver.id}
                        onClick={() => handleSelectVersion(ver)}
                        className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-xs transition-colors hover:bg-zinc-800/70 ${
                          isViewing
                            ? 'bg-indigo-500/10 border border-indigo-500/20'
                            : 'bg-transparent'
                        }`}
                      >
                        <div className="flex items-center gap-1.5">
                          <span className={`font-medium ${
                            isViewing ? 'text-indigo-400' : 'text-zinc-300'
                          }`}>
                            v{ver.version_number}
                          </span>
                          {isCurrentVer && (
                            <span className="text-emerald-400/60 text-[10px]">current</span>
                          )}
                          {isViewing && !isCurrentVer && (
                            <span className="text-amber-400/60 text-[10px]">viewing</span>
                          )}
                        </div>
                        <span className="text-zinc-500 text-[10px]">
                          {formatDate(ver.created_at)}
                        </span>
                      </button>
                    );
                  })}
              </div>
            </div>
          )}
          </div>
          ) : (
            <ResourceReviewPanel
              resourceId={resourceId}
              versionId={selectedVersionId || viewingVersion?.id}
              currentUserId={currentUserId}
              currentTime={videoCurrentTime}
              isVideo={!!isVideo}
              onSeekTo={(s) => { if (videoRef.current) videoRef.current.currentTime = s; }}
              onStartAnnotation={() => { setAnnotationActive(true); setViewAnnotations(undefined); }}
              pendingAnnotations={pendingAnnotations}
              onClearAnnotations={() => setPendingAnnotations([])}
              onViewAnnotations={(annotations) => {
                setViewAnnotations(annotations.map(a => ({ tool_type: a.tool_type, data: a.data })));
              }}
              onCommentChange={refreshCommentMarkers}
            />
          )}
        </div>
      </div>

      {/* Share Modal */}
      {showShareModal && (
        <ShareModal
          isOpen={true}
          onClose={() => setShowShareModal(false)}
          resourceId={resourceId}
        />
      )}

      {/* Version Manager Modal */}
      <VersionManagerModal
        isOpen={showVersionManager}
        onClose={() => setShowVersionManager(false)}
        resourceId={resourceId}
        currentVersionNumber={resource.current_version}
        onVersionChange={handleVersionChange}
      />

      {/* Keyboard Shortcuts Dialog */}
      <KeyboardShortcutsDialog
        isOpen={showShortcuts}
        onClose={() => setShowShortcuts(false)}
      />
    </div>
  );
};
