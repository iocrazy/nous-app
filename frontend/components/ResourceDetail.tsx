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
  RefreshCw,
  Search,
  Share2,
  Brain,
  Sparkles,
  Eye,
  AlertCircle,
  Clock,
  Tag as TagIcon,
  Check,
  Copy,
  Pencil,
  Star,
  FolderOpen,
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
  updateResource,
} from '../services/resourceService';
import { fetchTags } from '../services/tagsService';
import { createTag } from '../services/unifiedTagService';
import { EagleTagPicker } from './EagleTagPicker';
import { getSupabaseAccessToken, getSupabaseClient } from '../supabaseClient';
import { formatDateLocalized } from '../utils/formatDate';
import { downloadFile } from '../utils/download';
import { useToast } from './Toast';
import { ShareModal } from './ShareModal';
import { VersionManagerModal } from './VersionManagerModal';
import VideoPlayer from './VideoPlayer';
import { KeyboardShortcutsDialog } from './KeyboardShortcutsDialog';
import { ResourceReviewPanel } from './ResourceReviewPanel';
import { ResourceAnnotationOverlay, NormalizedAnnotation } from './ResourceAnnotationOverlay';
import { AudioWaveformPlayer } from './AudioWaveformPlayer';
import { fetchComments } from '../services/reviewService';
import {
  triggerTranscriptionByResource,
  getTranscriptByResource,
  triggerSummaryByResource,
  getSummaryByResource,
  triggerVisualAnalysisByResource,
  pollForResult,
} from '../services/aiService';
import { TranscriptData, SummaryData } from '../types';

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

function formatTimestamp(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function generateSRT(segments: TranscriptData['segments']): string {
  return segments.map((seg, i) => {
    const fmt = (sec: number) => {
      const h = Math.floor(sec / 3600);
      const m = Math.floor((sec % 3600) / 60);
      const s = Math.floor(sec % 60);
      const ms = Math.round((sec % 1) * 1000);
      return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')},${ms.toString().padStart(3, '0')}`;
    };
    return `${i + 1}\n${fmt(seg.start)} --> ${fmt(seg.end)}\n${seg.text}`;
  }).join('\n\n');
}

function downloadTextFile(content: string, filename: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function getAIStatusIndicator(status?: string) {
  switch (status) {
    case 'processing':
      return <span className="w-2 h-2 rounded-full bg-indigo-400 animate-pulse" />;
    case 'completed':
      return <span className="w-2 h-2 rounded-full bg-emerald-400" />;
    case 'failed':
      return <span className="w-2 h-2 rounded-full bg-red-400" />;
    default:
      return null;
  }
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

// ─── InfoRow (matches ResourceInfoPanel) ────────────────

const InfoRow = ({ label, value, children }: { label: string; value?: string | null | undefined; children?: React.ReactNode }) => {
  if (!value && !children) return null;
  return (
    <div className="flex justify-between items-center py-1.5">
      <span className="text-xs text-zinc-500">{label}</span>
      {children || <span className="text-xs text-zinc-300 text-right">{value}</span>}
    </div>
  );
};

// ─── Star Rating ────────────────────────────────────────

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

// ─── Main Component ──────────────────────────────────────

interface ResourceDetailProps {
  resourceId: string;
}

export const ResourceDetail: React.FC<ResourceDetailProps> = ({ resourceId }) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
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
  const [originalFileUrl, setOriginalFileUrl] = useState<string | null>(null);
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

  // Inspector state — editable fields (synced with resource)
  const [editingName, setEditingName] = useState(false);
  const [nameValue, setNameValue] = useState('');
  const [notesValue, setNotesValue] = useState('');
  const [urlValue, setUrlValue] = useState('');
  const nameInputRef = useRef<HTMLInputElement>(null);

  // Review state
  const [rightTab, setRightTab] = useState<'info' | 'review' | 'transcript' | 'analysis'>('info');
  const [currentUserId, setCurrentUserId] = useState<string>('');

  // AI / Transcript / Analysis state
  const [transcript, setTranscript] = useState<TranscriptData | null>(null);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [visualAnalysisLoading, setVisualAnalysisLoading] = useState(false);
  const [visualAnalysisError, setVisualAnalysisError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
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
      navigate(`${teamId ? `/team/${teamId}` : ''}/resources/file/${target.resource.id}`);
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

  // Sync editable fields when resource loads/changes
  useEffect(() => {
    if (resource) {
      setNameValue(resource.filename);
      setNotesValue(resource.notes || '');
      setUrlValue(resource.url || '');
      setEditingName(false);
    }
  }, [resource?.id, resource?.filename, resource?.notes, resource?.url]);

  useEffect(() => {
    if (editingName) nameInputRef.current?.select();
  }, [editingName]);

  // ─── Resource field update handler ───────────────────
  const handleResourceUpdate = useCallback(async (data: Partial<Resource>) => {
    if (!resource) return;
    try {
      await updateResource(resourceId, data as Parameters<typeof updateResource>[1]);
      setResource(prev => prev ? { ...prev, ...data } : prev);
    } catch (err) {
      console.error('Failed to update resource:', err);
    }
  }, [resourceId, resource]);

  const commitName = useCallback(() => {
    setEditingName(false);
    const trimmed = nameValue.trim();
    if (trimmed && trimmed !== resource?.filename) {
      handleResourceUpdate({ filename: trimmed });
    } else if (resource) {
      setNameValue(resource.filename);
    }
  }, [nameValue, resource?.filename, handleResourceUpdate]);

  const commitNotes = useCallback(() => {
    const val = notesValue.trim();
    if (val !== (resource?.notes || '').trim()) {
      handleResourceUpdate({ notes: val || null } as Partial<Resource>);
    }
  }, [notesValue, resource?.notes, handleResourceUpdate]);

  const commitUrl = useCallback(() => {
    const val = urlValue.trim();
    if (val !== (resource?.url || '').trim()) {
      handleResourceUpdate({ url: val || null } as Partial<Resource>);
    }
  }, [urlValue, resource?.url, handleResourceUpdate]);

  const handleRating = useCallback((v: number) => {
    handleResourceUpdate({ rating: v });
  }, [handleResourceUpdate]);

  // Close version dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
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
          // Also provide direct file URL for "Original" quality option
          setOriginalFileUrl(getVersionFileUrl(resourceId, viewingVersion.id, token || undefined));
        } else if (selectedVersionId) {
          setFileUrl(getVersionFileUrl(resourceId, selectedVersionId, token || undefined));
          setOriginalFileUrl(null);
        } else {
          setFileUrl(getResourceFileUrl(resourceId, token || undefined));
          setOriginalFileUrl(null);
        }
      } catch {
        if (cancelled) return;
        setOriginalFileUrl(null);
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
    setAssignedTags([]);
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
      setAssignedTags((prev) => prev.filter((t) => String(t.tag?.id) !== tagId));
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

  // ─── AI: load existing transcript/summary on tab switch ───
  useEffect(() => {
    if (rightTab === 'transcript' && resource?.transcript_status === 'completed' && !transcript) {
      loadTranscript();
    }
    if (rightTab === 'analysis' && resource?.summary_status === 'completed' && !summary) {
      loadSummary();
    }
  }, [rightTab, resource?.transcript_status, resource?.summary_status]);

  const loadTranscript = useCallback(async () => {
    try {
      setTranscriptLoading(true);
      setTranscriptError(null);
      const data = await getTranscriptByResource(resourceId);
      setTranscript(data);
    } catch (err) {
      setTranscriptError(err instanceof Error ? err.message : 'Failed to load transcript');
    } finally {
      setTranscriptLoading(false);
    }
  }, [resourceId]);

  const loadSummary = useCallback(async () => {
    try {
      setSummaryLoading(true);
      setSummaryError(null);
      const data = await getSummaryByResource(resourceId);
      setSummary(data);
    } catch (err) {
      setSummaryError(err instanceof Error ? err.message : 'Failed to load summary');
    } finally {
      setSummaryLoading(false);
    }
  }, [resourceId]);

  const handleTranscribe = async () => {
    try {
      setTranscriptLoading(true);
      setTranscriptError(null);
      await triggerTranscriptionByResource(resourceId);
      // Poll for completion
      const data = await pollForResult(() => getTranscriptByResource(resourceId), 3000, 60);
      setTranscript(data);
    } catch (err) {
      setTranscriptError(err instanceof Error ? err.message : 'Failed to transcribe');
    } finally {
      setTranscriptLoading(false);
    }
  };

  const handleSummarize = async () => {
    try {
      setSummaryLoading(true);
      setSummaryError(null);
      await triggerSummaryByResource(resourceId);
      const data = await pollForResult(() => getSummaryByResource(resourceId), 3000, 60);
      setSummary(data);
    } catch (err) {
      setSummaryError(err instanceof Error ? err.message : 'Failed to summarize');
    } finally {
      setSummaryLoading(false);
    }
  };

  const handleVisualAnalysis = async () => {
    try {
      setVisualAnalysisLoading(true);
      setVisualAnalysisError(null);
      await triggerVisualAnalysisByResource(resourceId);
    } catch (err) {
      setVisualAnalysisError(err instanceof Error ? err.message : 'Failed to start analysis');
    } finally {
      setVisualAnalysisLoading(false);
    }
  };

  const handleCopyTranscript = () => {
    if (!transcript) return;
    const text = transcript.segments
      .map((s) => `[${formatTimestamp(s.start)}] ${s.text}`)
      .join('\n');
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleExportSRT = () => {
    if (!transcript) return;
    const srt = generateSRT(transcript.segments);
    downloadTextFile(srt, `${resource?.filename || resourceId}_transcript.srt`, 'text/srt');
  };

  const handleExportTXT = () => {
    if (!transcript) return;
    downloadTextFile(transcript.text, `${resource?.filename || resourceId}_transcript.txt`, 'text/plain');
  };

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
  const fileExt = getFileExtension(resource.filename);

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
                    <button
                      className="block w-full text-left px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors"
                      onClick={() => {
                        setShowMoreMenu(false);
                        downloadFile(fileUrl, resource.filename || 'download', {
                          onSuccess: (f) => addToast(`Downloaded: ${f}`, 'success'),
                          onError: (msg) => addToast(`Download failed (${msg})`, 'error'),
                        });
                      }}
                    >
                      {t('resources.downloadOriginal')}
                    </button>
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
                        const basePath = teamId ? `/team/${teamId}` : '';
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
                originalSrc={originalFileUrl || undefined}
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
            {(isVideo || isAudio) && (
              <>
                <button
                  onClick={() => setRightTab('transcript')}
                  className={`flex-1 px-3 py-2 text-xs font-medium transition-colors flex items-center justify-center gap-1 ${
                    rightTab === 'transcript'
                      ? 'text-zinc-200 border-b-2 border-indigo-500'
                      : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  Transcript
                  {getAIStatusIndicator(resource.transcript_status)}
                </button>
                <button
                  onClick={() => setRightTab('analysis')}
                  className={`flex-1 px-3 py-2 text-xs font-medium transition-colors flex items-center justify-center gap-1 ${
                    rightTab === 'analysis'
                      ? 'text-zinc-200 border-b-2 border-indigo-500'
                      : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  Analysis
                  {getAIStatusIndicator(resource.summary_status || resource.visual_analysis_status)}
                </button>
              </>
            )}
          </div>

          {rightTab === 'info' ? (
          <div className="overflow-y-auto flex-1">
          {/* Editable Filename */}
          <div className="px-4 pt-4">
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
          <EagleTagPicker
            assignedTags={assignedTags.map(item => item.tag).filter((t): t is Tag => !!t)}
            allTags={allTags}
            onAdd={handleAddTag}
            onRemove={handleRemoveTag}
            onCreate={async (name, color) => {
              try {
                const tag = await createTag({ name, color, type: 'user' });
                setAllTags(prev => [...prev, tag]);
                return tag;
              } catch { return null; }
            }}
          />

          {/* Properties */}
          <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3">
            <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
              {t('resources.infoPanel.properties')}
            </h4>
            <div className="space-y-0">
              <InfoRow label={t('resources.infoPanel.rating')}>
                <StarRating value={resource.rating ?? 0} onChange={handleRating} />
              </InfoRow>
              {(isVideo || isAudio) && resource.duration_seconds != null && (
                <InfoRow label={t('resources.infoPanel.duration')} value={formatDuration(resource.duration_seconds)} />
              )}
              <InfoRow label={t('resources.infoPanel.size')} value={formatFileSize(resource.file_size_bytes)} />
              <InfoRow label={t('resources.infoPanel.type')} value={resource.file_type || resource.mime_type} />
              {isVideo && resource.resolution && (
                <InfoRow label={t('resources.infoPanel.resolution')} value={resource.resolution?.replace(/:/g, 'x')} />
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

          {/* Source link */}
          {resource.media_id && (
            <div className="px-4 mt-3 border-t border-zinc-800/60 pt-3">
              <a
                href={`/resources/media/${resource.media_id}`}
                className="inline-flex items-center gap-1.5 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
                target="_blank"
                rel="noopener noreferrer"
              >
                <ExternalLink size={12} />
                {t('resources.viewOriginal')}
              </a>
            </div>
          )}

          {/* Versions */}
          {versions.length > 0 && (
            <div className="px-4 mt-3 border-t border-zinc-800/60 pt-3">
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

          {/* Bottom spacing */}
          <div className="h-6" />
          </div>
          ) : rightTab === 'review' ? (
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
          ) : rightTab === 'transcript' ? (
            <div className="overflow-y-auto flex-1 p-4 space-y-4 animate-in fade-in duration-300">
              {/* Processing state */}
              {resource.transcript_status === 'processing' && (
                <div className="flex flex-col items-center justify-center py-16 text-center">
                  <Loader2 size={32} className="animate-spin text-indigo-400 mb-4" />
                  <h3 className="text-sm font-medium text-zinc-200">Transcribing...</h3>
                  <p className="text-xs text-zinc-500 mt-1">This may take a few minutes.</p>
                </div>
              )}

              {/* Not started */}
              {(!resource.transcript_status || resource.transcript_status === 'pending' || resource.transcript_status === 'none') && !transcript && !transcriptLoading && (
                <div className="flex flex-col items-center justify-center py-16 text-center">
                  <div className="p-4 bg-zinc-800/50 rounded-full mb-4">
                    <FileText size={28} className="text-zinc-500" />
                  </div>
                  <h3 className="text-sm font-medium text-zinc-200">No Transcript Available</h3>
                  <p className="text-xs text-zinc-500 mt-1 mb-4 max-w-[220px]">
                    Generate a transcript to see timestamped text from this media.
                  </p>
                  <button
                    onClick={handleTranscribe}
                    disabled={transcriptLoading}
                    className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center gap-2 disabled:opacity-50"
                  >
                    {transcriptLoading ? <Loader2 size={14} className="animate-spin" /> : <Brain size={14} />}
                    Transcribe
                  </button>
                  {transcriptError && (
                    <div className="mt-3 flex items-center gap-1.5 text-xs text-red-400">
                      <AlertCircle size={12} />
                      {transcriptError}
                    </div>
                  )}
                </div>
              )}

              {/* Failed */}
              {resource.transcript_status === 'failed' && !transcript && (
                <div className="flex flex-col items-center justify-center py-16 text-center">
                  <div className="p-4 bg-red-500/10 rounded-full mb-4">
                    <AlertCircle size={28} className="text-red-400" />
                  </div>
                  <h3 className="text-sm font-medium text-zinc-200">Transcription Failed</h3>
                  <p className="text-xs text-zinc-500 mt-1 mb-4">Something went wrong. Please try again.</p>
                  <button
                    onClick={handleTranscribe}
                    disabled={transcriptLoading}
                    className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center gap-2 disabled:opacity-50"
                  >
                    {transcriptLoading ? <Loader2 size={14} className="animate-spin" /> : <Brain size={14} />}
                    Retry
                  </button>
                </div>
              )}

              {/* Loading existing */}
              {transcriptLoading && resource.transcript_status === 'completed' && (
                <div className="flex items-center justify-center py-16">
                  <Loader2 size={20} className="animate-spin text-indigo-400" />
                </div>
              )}

              {/* Transcript content */}
              {transcript && (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center gap-2 text-[10px] text-zinc-500">
                    <span className="flex items-center gap-1">
                      <Clock size={10} />
                      {formatTimestamp(transcript.duration)} total
                    </span>
                    <span className="flex items-center gap-1">
                      <TagIcon size={10} />
                      {transcript.language.toUpperCase()}
                    </span>
                    <span>{transcript.segments.length} segments</span>
                  </div>

                  <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
                    <div className="max-h-[50vh] overflow-y-auto custom-scrollbar divide-y divide-zinc-800/50">
                      {transcript.segments.map((seg, i) => (
                        <div
                          key={i}
                          className="flex gap-2 px-3 py-2 hover:bg-zinc-800/30 transition-colors group"
                        >
                          <button
                            className="text-[10px] font-mono text-indigo-400/70 group-hover:text-indigo-400 shrink-0 pt-0.5 transition-colors"
                            onClick={() => { if (videoRef.current) videoRef.current.currentTime = seg.start; }}
                          >
                            [{formatTimestamp(seg.start)}]
                          </button>
                          <p className="text-xs text-zinc-300 leading-relaxed">{seg.text}</p>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-1.5">
                    <button
                      onClick={handleCopyTranscript}
                      className="px-3 py-1.5 text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors flex items-center gap-1.5 border border-zinc-700"
                    >
                      {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
                      {copied ? 'Copied!' : 'Copy'}
                    </button>
                    <button
                      onClick={handleExportSRT}
                      className="px-3 py-1.5 text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors flex items-center gap-1.5 border border-zinc-700"
                    >
                      <Download size={12} />
                      SRT
                    </button>
                    <button
                      onClick={handleExportTXT}
                      className="px-3 py-1.5 text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors flex items-center gap-1.5 border border-zinc-700"
                    >
                      <Download size={12} />
                      TXT
                    </button>
                  </div>
                </div>
              )}
            </div>
          ) : rightTab === 'analysis' ? (
            <div className="overflow-y-auto flex-1 p-4 space-y-5 animate-in fade-in duration-300">
              {/* Summary Section */}
              <section className="space-y-2">
                <div className="flex items-center gap-2">
                  <div className="p-1 bg-indigo-500/10 rounded text-indigo-400">
                    <Sparkles size={14} />
                  </div>
                  <h3 className="text-xs font-medium text-zinc-200">Summary</h3>
                  {getAIStatusIndicator(resource.summary_status)}
                </div>

                {resource.summary_status === 'processing' && (
                  <div className="flex items-center gap-2 p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                    <Loader2 size={14} className="animate-spin text-indigo-400" />
                    <span className="text-xs text-zinc-400">Generating summary...</span>
                  </div>
                )}

                {(!resource.summary_status || resource.summary_status === 'pending' || resource.summary_status === 'none') && !summary && !summaryLoading && (
                  <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                    <p className="text-xs text-zinc-500 mb-2">Generate an AI summary with key points and topics.</p>
                    <button
                      onClick={handleSummarize}
                      disabled={summaryLoading}
                      className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5 disabled:opacity-50"
                    >
                      {summaryLoading ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                      Summarize
                    </button>
                    {summaryError && (
                      <p className="mt-2 text-[10px] text-red-400 flex items-center gap-1">
                        <AlertCircle size={10} />
                        {summaryError}
                      </p>
                    )}
                  </div>
                )}

                {resource.summary_status === 'failed' && !summary && (
                  <div className="p-3 bg-red-500/5 border border-red-500/20 rounded-lg">
                    <p className="text-xs text-red-400 mb-2">Summarization failed.</p>
                    <button
                      onClick={handleSummarize}
                      disabled={summaryLoading}
                      className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5 disabled:opacity-50"
                    >
                      {summaryLoading ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                      Retry
                    </button>
                  </div>
                )}

                {summaryLoading && resource.summary_status === 'completed' && (
                  <div className="flex items-center justify-center py-6">
                    <Loader2 size={16} className="animate-spin text-indigo-400" />
                  </div>
                )}

                {summary && (
                  <div className="space-y-3">
                    <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                      <p className="text-xs text-zinc-300 leading-relaxed">{summary.summary}</p>
                    </div>
                    {summary.key_points.length > 0 && (
                      <div>
                        <h4 className="text-[10px] font-medium text-zinc-400 uppercase tracking-wider mb-1.5">Key Points</h4>
                        <ul className="space-y-1">
                          {summary.key_points.map((point, i) => (
                            <li key={i} className="flex items-start gap-1.5 text-xs text-zinc-300">
                              <ChevronRight size={12} className="text-indigo-400 mt-0.5 shrink-0" />
                              {point}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {summary.topics.length > 0 && (
                      <div>
                        <h4 className="text-[10px] font-medium text-zinc-400 uppercase tracking-wider mb-1.5">Topics</h4>
                        <div className="flex flex-wrap gap-1.5">
                          {summary.topics.map((topic, i) => {
                            const colors = [
                              'bg-indigo-500/10 text-indigo-400 border-indigo-500/20',
                              'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
                              'bg-purple-500/10 text-purple-400 border-purple-500/20',
                              'bg-amber-500/10 text-amber-400 border-amber-500/20',
                              'bg-rose-500/10 text-rose-400 border-rose-500/20',
                              'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
                            ];
                            return (
                              <span key={i} className={`px-2 py-0.5 rounded-full text-[10px] font-medium border ${colors[i % colors.length]}`}>
                                {topic}
                              </span>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </section>

              {/* Visual Analysis Section (video only) */}
              {isVideo && (
                <section className="space-y-2">
                  <div className="flex items-center gap-2">
                    <div className="p-1 bg-purple-500/10 rounded text-purple-400">
                      <Eye size={14} />
                    </div>
                    <h3 className="text-xs font-medium text-zinc-200">Visual Analysis</h3>
                    {getAIStatusIndicator(resource.visual_analysis_status)}
                  </div>

                  {resource.visual_analysis_status === 'processing' && (
                    <div className="flex items-center gap-2 p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                      <Loader2 size={14} className="animate-spin text-purple-400" />
                      <span className="text-xs text-zinc-400">Analyzing visual content...</span>
                    </div>
                  )}

                  {(!resource.visual_analysis_status || resource.visual_analysis_status === 'pending' || resource.visual_analysis_status === 'none') && (
                    <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                      <p className="text-xs text-zinc-500 mb-2">Analyze video frames to detect objects, scenes, and visual content.</p>
                      <button
                        onClick={handleVisualAnalysis}
                        disabled={visualAnalysisLoading}
                        className="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5 disabled:opacity-50"
                      >
                        {visualAnalysisLoading ? <Loader2 size={12} className="animate-spin" /> : <Eye size={12} />}
                        Analyze
                      </button>
                      {visualAnalysisError && (
                        <p className="mt-2 text-[10px] text-red-400 flex items-center gap-1">
                          <AlertCircle size={10} />
                          {visualAnalysisError}
                        </p>
                      )}
                    </div>
                  )}

                  {resource.visual_analysis_status === 'failed' && (
                    <div className="p-3 bg-red-500/5 border border-red-500/20 rounded-lg">
                      <p className="text-xs text-red-400 mb-2">Visual analysis failed.</p>
                      <button
                        onClick={handleVisualAnalysis}
                        disabled={visualAnalysisLoading}
                        className="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5 disabled:opacity-50"
                      >
                        {visualAnalysisLoading ? <Loader2 size={12} className="animate-spin" /> : <Eye size={12} />}
                        Retry
                      </button>
                    </div>
                  )}

                  {resource.visual_analysis_status === 'completed' && (resource as any).ai_analyze_text && (
                    <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-lg">
                      <p className="text-xs text-zinc-300 leading-relaxed whitespace-pre-wrap">
                        {(resource as any).ai_analyze_text}
                      </p>
                    </div>
                  )}
                </section>
              )}
            </div>
          ) : null}
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
