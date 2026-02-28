
import React, { useState, useRef, useEffect, useCallback } from 'react';
import Hls from 'hls.js';
import { Video, DownloadStatus, Collection } from '../types';
import {
  Heart, MessageCircle, Share2, Bookmark, Download, Music, Image as ImageIcon, Video as VideoIcon, User, Tag, ChevronLeft, ChevronRight,
  Clock, Timer, Copy, PenTool, FileText, Wand2, Check, Loader2, Play, RefreshCw, Trash2, X, FolderPlus, Plus,
  Sparkles, Eye, ExternalLink, MoreHorizontal, Star,
} from 'lucide-react';
import { isVideoType, getAwemeTypeLabel, getVideoUrl, getCoverUrl, isPlayableUrl, formatResolution } from '../utils/awemeType';
import { getDownloadUrl, getCoverDownloadUrl } from '../services/dataService';
import { downloadFile, downloadWithAuth } from '../utils/download';
import { getAuthHeaders, parseShareLink, fetchMediaByType } from '../services/parserService';
import { CollectionPicker } from './CollectionPicker';
import { DownloadProgress, DownloadStatus as ProgressStatus, ProgressStyleType } from './DownloadProgress';
import { useToast } from './Toast';
import {
  triggerTranscription, getTranscript,
  triggerSummary, getSummary,
  triggerVisualAnalysis,
} from '../services/aiService';
import { fetchResourceTags, addResourceTag, removeResourceTag } from '../services/resourceService';
import { fetchAllTags, createTag } from '../services/unifiedTagService';
import { getSupabaseClient } from '../supabaseClient';
import { UnifiedTagPicker } from './UnifiedTagPicker';

interface MediaCardProps {
  data: Video;
  onSave?: (data: Video) => void;
  onUpdate?: (id: string, updates: Partial<Video>) => void;
  onDelete?: (id: string, deleteFiles: boolean) => Promise<void>;
  collections?: Collection[];
  videoCollectionIds?: string[];
  onToggleCollection?: (collectionId: string) => void;
  onCreateCollection?: (name: string, teamId: string | null) => Promise<void>;
  // Download progress props for showing progress in the media preview area
  downloadStatus?: ProgressStatus;
  downloadPercent?: number;
  downloadSpeed?: string;
  progressStyle?: ProgressStyleType;
  /** Hide the left-side media preview (used when an external player is already shown) */
  hidePreview?: boolean;
  // Resource-level rating & notes (from resources table)
  resourceRating?: number;
  resourceNotes?: string;
  onRatingChange?: (rating: number) => void;
  onNotesChange?: (notes: string) => void;
  onNotesBlur?: () => void;
}

// Helper to generate consistent colors from strings (Shared logic)
const getTagStyle = (tag: string) => {
  const styles = [
    'bg-rose-500/10 text-rose-400 border-rose-500/20',
    'bg-orange-500/10 text-orange-400 border-orange-500/20',
    'bg-amber-500/10 text-amber-400 border-amber-500/20',
    'bg-yellow-500/10 text-yellow-400 border-yellow-500/20',
    'bg-lime-500/10 text-lime-400 border-lime-500/20',
    'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
    'bg-teal-500/10 text-teal-400 border-teal-500/20',
    'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
    'bg-sky-500/10 text-sky-400 border-sky-500/20',
    'bg-indigo-500/10 text-indigo-400 border-indigo-500/20',
    'bg-violet-500/10 text-violet-400 border-violet-500/20',
    'bg-purple-500/10 text-purple-400 border-purple-500/20',
    'bg-fuchsia-500/10 text-fuchsia-400 border-fuchsia-500/20',
    'bg-pink-500/10 text-pink-400 border-pink-500/20',
  ];

  let hash = 0;
  for (let i = 0; i < tag.length; i++) {
    hash = tag.charCodeAt(i) + ((hash << 5) - hash);
  }
  return styles[Math.abs(hash) % styles.length];
};

// Helper to get AI status icon styling
const getAIStatusClass = (status?: string): string => {
  switch (status) {
    case 'processing':
      return 'animate-spin text-indigo-400';
    case 'completed':
      return 'text-emerald-400';
    case 'failed':
      return 'text-red-400';
    default:
      return 'text-zinc-600';
  }
};

export const MediaCard: React.FC<MediaCardProps> = ({
  data,
  onSave,
  onUpdate,
  onDelete,
  collections = [],
  videoCollectionIds = [],
  onToggleCollection,
  onCreateCollection,
  downloadStatus,
  downloadPercent = 0,
  downloadSpeed,
  progressStyle = 'neon',
  hidePreview = false,
  resourceRating,
  resourceNotes,
  onRatingChange,
  onNotesChange,
  onNotesBlur,
}) => {
  const [currentImageIndex, setCurrentImageIndex] = useState(0);
  const [copied, setCopied] = useState(false);
  const [copiedShare, setCopiedShare] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [retrySuccess, setRetrySuccess] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [showDownloadMenu, setShowDownloadMenu] = useState(false);
  const [showMoreMenu, setShowMoreMenu] = useState(false);
  const [showCollectionPicker, setShowCollectionPicker] = useState(false);

  // HLS video ref
  const videoRef = useRef<HTMLVideoElement>(null);
  const hlsRef = useRef<Hls | null>(null);

  // Delete confirmation dialog states
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  // AI Feature States - 从数据中加载已有内容
  const [rewrittenText, setRewrittenText] = useState<string | null>(data.ai_rewrite_text || null);
  const [analysisText, setAnalysisText] = useState<string | null>(data.ai_analyze_text || null);
  const [extractText, setExtractText] = useState<string | null>(data.ai_extract_text || null);
  const [loadingAction, setLoadingAction] = useState<string | null>(null);

  // Resource tags (fetched from resource_tags junction table)
  const [resourceTags, setResourceTags] = useState<Array<{ tag: { id: string; name: string; color?: string } }>>([]);
  const [resourceId, setResourceId] = useState<string | null>(null);
  const [allTags, setAllTags] = useState<import('../types').Tag[]>([]);

  useEffect(() => {
    fetchAllTags().then(setAllTags).catch(() => {});
  }, []);

  useEffect(() => {
    if (!data.id) return;
    let cancelled = false;
    (async () => {
      const supabase = getSupabaseClient();
      if (!supabase) return;
      const { data: resource } = await supabase
        .from('resources')
        .select('id')
        .eq('media_id', data.id)
        .limit(1)
        .maybeSingle();
      if (cancelled || !resource) return;
      const resId = String(resource.id);
      if (!cancelled) setResourceId(resId);
      try {
        const tags = await fetchResourceTags(resId);
        if (!cancelled) setResourceTags(tags);
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [data.id]);

  const handleAddTag = useCallback(async (tagId: string) => {
    if (!resourceId) return;
    try {
      await addResourceTag(resourceId, tagId);
      const updated = await fetchResourceTags(resourceId);
      setResourceTags(updated);
    } catch (err) {
      console.error('Failed to add tag:', err);
    }
  }, [resourceId]);

  const handleRemoveTag = useCallback(async (tagId: string) => {
    if (!resourceId) return;
    try {
      await removeResourceTag(resourceId, tagId);
      setResourceTags(prev => prev.filter(t => t.tag?.id !== tagId));
    } catch (err) {
      console.error('Failed to remove tag:', err);
    }
  }, [resourceId]);

  const handleCreateTag = useCallback(async (name: string, color: string) => {
    try {
      const tag = await createTag({ name, color, type: 'user' });
      setAllTags(prev => [...prev, tag]);
      return tag;
    } catch {
      return null;
    }
  }, []);

  // Track fetch submissions (optimistic UI: hide menu items after submit)
  const [fetchSubmitted, setFetchSubmitted] = useState({ video: false, cover: false, music: false });

  const isVideo = isVideoType(data.media_type);
  const images = data.image_download_urls || [];
  const isAlbum = !isVideo && images.length > 1;

  // 视频 URL: 优先使用 download_path
  const videoUrl = getVideoUrl(data);
  // 封面 URL
  const coverUrl = getCoverUrl(data);

  // 判断内容是否已可用（本地已下载 OR CDN URL 已存在 OR 本次已提交 Fetch）
  // 注意：skipped ≠ 已可用（skipped 表示当初跳过，用户可重新获取）
  // download_path 存在但 status 非 completed 时（如被重置为 pending），不算已下载
  const videoStatusCompleted = data.video_download_status?.toLowerCase() === 'completed';
  const videoDownloaded = fetchSubmitted.video
    || (!!data.download_path && videoStatusCompleted)
    || !!(data.video_download_urls?.[0] && data.video_download_urls[0] !== '#' && isPlayableUrl(data.video_download_urls[0]));
  const coverDownloaded = fetchSubmitted.cover || !!data.cover_download_path
    || !!(data.cover_urls?.[0] && data.cover_urls[0] !== '#');
  const musicDownloaded = fetchSubmitted.music || !!data.music_download_path
    || !!(data.music_download_urls?.[0] && data.music_download_urls[0] !== '#');

  // Setup HLS.js for .m3u8 video playback
  useEffect(() => {
    if (!isPlaying || !videoRef.current || !videoUrl) return;

    const isHls = videoUrl.endsWith('.m3u8');
    if (!isHls) return;

    if (Hls.isSupported()) {
      const hls = new Hls();
      hls.loadSource(videoUrl);
      hls.attachMedia(videoRef.current);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        videoRef.current?.play();
      });
      hlsRef.current = hls;
    } else if (videoRef.current.canPlayType('application/vnd.apple.mpegurl')) {
      // Native HLS support (Safari)
      videoRef.current.src = videoUrl;
      videoRef.current.play();
    }

    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
    };
  }, [isPlaying, videoUrl]);

  // 执行删除
  const handleDelete = async () => {
    if (!onDelete || !data.platform_id) return;
    setIsDeleting(true);
    try {
      await onDelete(data.platform_id, false);
      setShowDeleteDialog(false);
    } catch (error) {
      console.error('Delete failed:', error);
    } finally {
      setIsDeleting(false);
    }
  };

  const handleSlide = (direction: 'left' | 'right') => {
    if (direction === 'left') {
        setCurrentImageIndex(prev => (prev === 0 ? images.length - 1 : prev - 1));
    } else {
        setCurrentImageIndex(prev => (prev === images.length - 1 ? 0 : prev + 1));
    }
  };

  // Format numbers (e.g., 12500 -> 12.5k)
  const formatNumber = (num?: number) => {
    if (!num) return '0';
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
    return num.toString();
  };

  const formatDateTime = (isoString?: string) => {
    if (!isoString) return 'N/A';
    try {
      const date = new Date(isoString);
      const pad = (n: number) => n.toString().padStart(2, '0');
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
    } catch (e) { return isoString; }
  };

  const formatDuration = (secondsStr?: string) => {
     if (!secondsStr) return '0s';
     return `${secondsStr}s`;
  };

  const dlCallbacks = {
    onSuccess: (fname: string) => addToast(`Downloaded: ${fname}`, 'success'),
    onError: (msg: string) => addToast(`Download failed (${msg})`, 'error'),
  };
  const dlSilent = {
    onSuccess: (fname: string) => addToast(`Downloaded: ${fname}`, 'success'),
  };

  const doDownload = async (url: string, filename: string, auth = false, silent = false): Promise<boolean> => {
    setIsDownloading(true);
    try {
      const cbs = silent ? dlSilent : dlCallbacks;
      return auth
        ? await downloadWithAuth(url, filename, cbs)
        : await downloadFile(url, filename, cbs);
    } finally {
      setIsDownloading(false);
    }
  };

  const onDownloadVideo = async () => {
    setShowDownloadMenu(false);
    if (!data.platform_id) return;
    // Try backend API first (silent error), fallback to remote URL
    if (data.download_path && data.video_download_status?.toLowerCase() === 'completed') {
      const ok = await doDownload(getDownloadUrl(data.platform_id), `${data.platform_id}.mp4`, true, true);
      if (ok) return;
    }
    if (videoUrl) {
      await doDownload(videoUrl, `${data.platform_id}.mp4`);
    } else {
      addToast('No video file available', 'error');
    }
  };

  const onDownloadCoverFile = async () => {
    setShowDownloadMenu(false);
    if (!data.platform_id) return;
    if (data.cover_download_path && data.cover_download_status?.toLowerCase() === 'completed') {
      const ok = await doDownload(getCoverDownloadUrl(data.platform_id), `${data.platform_id}_cover.jpg`, true, true);
      if (ok) return;
    }
    if (coverUrl) {
      await doDownload(coverUrl, `${data.platform_id}_cover.jpg`);
    } else {
      addToast('No cover file available', 'error');
    }
  };

  const onDownloadImages = () => {
    if (!data.image_download_urls) return;
    data.image_download_urls.forEach((url, idx) => {
      setTimeout(() => {
        doDownload(url, `${data.platform_id || 'image'}_${idx + 1}.jpg`);
      }, idx * 500);
    });
  };

  const onDownloadAudio = () => {
    const url = data.music_download_urls?.[0];
    if (!url || url === '#') return;
    doDownload(url, `${data.platform_id || 'music'}.mp3`);
  };

  const onDownloadCover = () => {
    if (!coverUrl) return;
    doDownload(coverUrl, `${data.platform_id || 'cover'}_cover.jpg`);
  };

  // 重新获取：使用 per-type fetch endpoint (POST /api/v1/videos/{platform_id}/fetch)
  // force=true 时跳过已下载检查，强制重新获取
  const onRefetch = async (options: { video?: boolean; music?: boolean; cover?: boolean; force?: boolean }) => {
    if (!data.platform_id) {
      addToast('No platform ID available for refetch', 'error');
      return;
    }

    // Pre-check: what's already available vs what needs fetching
    const alreadyHave: string[] = [];
    const types: string[] = [];

    if (options.video) {
      if (!options.force && videoDownloaded) alreadyHave.push('Video');
      else types.push('video');
    }
    if (options.cover) {
      if (!options.force && coverDownloaded) alreadyHave.push('Cover');
      else types.push('cover');
    }
    if (options.music) {
      if (!options.force && musicDownloaded) alreadyHave.push('Audio');
      else types.push('music');
    }

    // If everything requested is already available, inform user and return
    if (types.length === 0) {
      addToast(`Already has: ${alreadyHave.join(', ')}`, 'info');
      setShowMoreMenu(false);
      return;
    }

    setIsRetrying(true);
    setRetrySuccess(false);
    setShowMoreMenu(false);

    try {
      const result = await fetchMediaByType(data.platform_id, types);
      setRetrySuccess(true);
      // Optimistic UI: mark submitted items so menu hides them immediately
      setFetchSubmitted(prev => ({
        ...prev,
        ...(types.includes('video') ? { video: true } : {}),
        ...(types.includes('cover') ? { cover: true } : {}),
        ...(types.includes('music') ? { music: true } : {}),
      }));
      const fetching: string[] = [];
      if (types.includes('video')) fetching.push('Video');
      if (types.includes('cover')) fetching.push('Cover');
      if (types.includes('music')) fetching.push('Audio');
      let msg = `Fetch submitted: ${fetching.join(', ')}`;
      if (alreadyHave.length > 0) msg += ` (already has: ${alreadyHave.join(', ')})`;
      if (result.types_skipped?.length) msg += ` (cached: ${result.types_skipped.join(', ')})`;
      addToast(msg, 'success');
      setTimeout(() => setRetrySuccess(false), 3000);
    } catch (error) {
      console.error('Refetch error:', error);
      addToast(error instanceof Error ? error.message : 'Refetch failed', 'error');
    } finally {
      setIsRetrying(false);
    }
  };



  // 保存 AI 内容到数据库
  const saveAIContent = (field: string, content: string) => {
    if (onUpdate && data.platform_id) {
      const updates: Partial<Video> = {
        [field]: content,
        ai_generated_at: new Date().toISOString()
      };
      onUpdate(data.platform_id, updates);
    }
  };

  const { addToast } = useToast();

  const handleAction = async (e: React.MouseEvent, action: string) => {
    e.stopPropagation();
    const platformId = data.platform_id;

    if (action === 'copy') {
      const textToCopy = data.description || data.title || "";
      if (textToCopy) {
        navigator.clipboard.writeText(textToCopy).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        });
      }
    } else if (action === 'extract') {
      if (!platformId) return;
      setLoadingAction('extract');
      try {
        // If already completed, fetch directly
        if (data.transcript_status === 'completed') {
          const result = await getTranscript(platformId);
          const text = result.text || '';
          setExtractText(text);
          saveAIContent('ai_extract_text', text);
          addToast('Transcription loaded', 'success');
        } else {
          // Trigger transcription task — don't poll, let backend process async
          await triggerTranscription(platformId);
          if (onUpdate) onUpdate(platformId, { transcript_status: 'processing' });
          addToast('Transcription started. Check back shortly.', 'info');
        }
      } catch (err: any) {
        addToast(err?.message || 'Transcription failed', 'error');
      } finally {
        setLoadingAction(null);
      }
    } else if (action === 'rewrite') {
      if (!platformId) return;
      setLoadingAction('rewrite');
      try {
        // If already completed, fetch directly
        if (data.summary_status === 'completed') {
          const result = await getSummary(platformId);
          const text = result.summary + (result.key_points?.length ? '\n\nKey Points:\n' + result.key_points.map(p => `- ${p}`).join('\n') : '');
          setRewrittenText(text);
          saveAIContent('ai_rewrite_text', text);
          addToast('Summary loaded', 'success');
        } else {
          // Trigger summary task — don't poll, let backend process async
          await triggerSummary(platformId);
          if (onUpdate) onUpdate(platformId, { summary_status: 'processing' });
          addToast('Summary started. Check back shortly.', 'info');
        }
      } catch (err: any) {
        addToast(err?.message || 'Summary failed', 'error');
      } finally {
        setLoadingAction(null);
      }
    } else if (action === 'analyze') {
      if (!platformId) return;
      setLoadingAction('analyze');
      try {
        await triggerVisualAnalysis(platformId);
        addToast('Visual analysis started', 'info');
      } catch (err: any) {
        const msg = err?.message || 'Visual analysis failed';
        // Handle 501 Not Implemented gracefully
        if (msg.includes('501') || msg.includes('Not Implemented')) {
          addToast('Visual analysis is not yet available', 'info');
        } else {
          addToast(msg, 'error');
        }
      } finally {
        setLoadingAction(null);
      }
    } else {
      console.log(`Action triggered: ${action}`);
    }
  };

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-2xl overflow-hidden hover:border-zinc-700 transition-all duration-300 shadow-lg flex flex-col max-w-full">
      <div className={`flex ${hidePreview ? 'flex-col' : 'flex-col md:flex-row'} min-w-0`}>
        {/* Media Preview Section - Left Side */}
        {!hidePreview && <div className="md:w-2/5 bg-black relative h-64 md:h-auto md:max-h-[70vh] md:min-h-[400px] group flex-shrink-0 flex items-center justify-center">
          {/* Show download progress when downloading */}
          {downloadStatus && (downloadStatus === 'downloading' || downloadStatus === 'pending') ? (
            <div className="w-full h-full flex items-center justify-center">
              <DownloadProgress
                percent={downloadPercent}
                status={downloadStatus}
                speed={downloadSpeed}
                style={progressStyle}
                thumbnailUrl={coverUrl || undefined}
              />
            </div>
          ) : isVideo ? (
            <div
              className="w-full h-full flex items-center justify-center bg-zinc-900 text-zinc-500 cursor-pointer relative overflow-hidden"
              onClick={() => setIsPlaying(true)}
            >
               {isPlaying ? (
                 <video
                   ref={videoRef}
                   src={videoUrl?.endsWith('.m3u8') ? undefined : videoUrl}
                   className="w-full h-full object-contain bg-black"
                   controls
                   autoPlay
                   playsInline
                 />
               ) : (
                 <>
                   <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent z-10 pointer-events-none" />
                   <img
                    src={coverUrl || "https://picsum.photos/400/600"}
                    alt="Thumbnail"
                    className="w-full h-full object-contain opacity-90"
                    referrerPolicy="no-referrer"
                   />
                   <div className="absolute z-20 w-16 h-16 bg-white/10 backdrop-blur-md rounded-full flex items-center justify-center group-hover:scale-110 transition-transform border border-white/20 shadow-xl">
                      <Play className="w-8 h-8 text-white fill-white ml-1" />
                   </div>
                 </>
               )}
            </div>
          ) : (
            <div className="w-full h-full relative">
               <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent z-10" />
               <img
                src={(isAlbum ? images[currentImageIndex] : images[0]) || "https://picsum.photos/400/600"}
                alt="Cover"
                className="w-full h-full object-cover transition-opacity duration-300"
                referrerPolicy="no-referrer"
               />

               {isAlbum ? (
                 <>
                    {/* Carousel Controls */}
                    <button
                      onClick={() => handleSlide('left')}
                      className="absolute left-4 top-1/2 -translate-y-1/2 p-2 bg-black/50 hover:bg-black/70 rounded-full text-white opacity-0 group-hover:opacity-100 transition-opacity z-30"
                    >
                      <ChevronLeft size={20} />
                    </button>
                    <button
                      onClick={() => handleSlide('right')}
                      className="absolute right-4 top-1/2 -translate-y-1/2 p-2 bg-black/50 hover:bg-black/70 rounded-full text-white opacity-0 group-hover:opacity-100 transition-opacity z-30"
                    >
                      <ChevronRight size={20} />
                    </button>
                    {/* Index Indicator */}
                    <div className="absolute top-4 left-4 z-20 bg-black/60 px-3 py-1 rounded-full text-xs text-white font-medium backdrop-blur-md">
                      {currentImageIndex + 1} / {images.length}
                    </div>
                 </>
               ) : (
                 <ImageIcon className="absolute top-4 right-4 z-20 w-6 h-6 text-white drop-shadow-md" />
               )}
            </div>
          )}

          {/* Hide author overlay when playing or downloading to prevent obstruction */}
          {!isPlaying && !(downloadStatus && (downloadStatus === 'downloading' || downloadStatus === 'pending')) && (
            <div className="absolute bottom-4 left-4 z-20 flex items-center space-x-2 text-white pointer-events-none">
                <User className="w-4 h-4" />
                <span className="font-medium text-sm drop-shadow-md">@{data.author || 'Unknown'}</span>
            </div>
          )}
        </div>}

        {/* Info Section - Right Side */}
        <div className="flex-1 p-4 sm:p-6 flex flex-col md:max-h-[70vh] overflow-y-auto custom-scrollbar">

          {/* Header Metadata */}
          <div className="flex justify-between items-start gap-2 mb-2 min-w-0">
            <div className="flex gap-2 shrink-0">
                <span className="px-2 py-1 text-xs font-semibold bg-zinc-800 text-zinc-300 rounded-md border border-zinc-700 uppercase tracking-wider">
                {getAwemeTypeLabel(data.media_type)}
                </span>
                {data.resolution && (
                <span className="px-2 py-1 text-xs font-semibold bg-indigo-900/30 text-indigo-400 rounded-md border border-indigo-900/50">
                {formatResolution(data.resolution)}
                </span>
                )}
            </div>
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-xs text-zinc-500 font-mono truncate min-w-0">ID: {data.id}</span>
              {/* More actions (three-dots) — hidden when inside PlayerPage */}
              {!hidePreview && (
              <div className="relative shrink-0">
                <button
                  onClick={() => setShowMoreMenu(!showMoreMenu)}
                  disabled={isRetrying}
                  className={`w-7 h-7 flex items-center justify-center rounded-md transition-colors ${
                    isRetrying
                      ? 'text-zinc-500'
                      : retrySuccess
                        ? 'text-emerald-400'
                        : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800'
                  } disabled:opacity-70`}
                  title="More Actions"
                >
                  {isRetrying ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : retrySuccess ? (
                    <Check className="w-4 h-4" />
                  ) : (
                    <MoreHorizontal className="w-4 h-4" />
                  )}
                </button>

                {showMoreMenu && (
                  <>
                    <div
                      className="fixed inset-0 z-40"
                      onClick={() => setShowMoreMenu(false)}
                    />
                    <div className="absolute top-full right-0 mt-1 w-48 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl z-50 overflow-hidden">
                      <div className="py-1">
                        {/* Refetch section — only show items NOT yet downloaded */}
                        {!videoDownloaded && (
                          <button
                            onClick={() => onRefetch({ video: true })}
                            className="w-full px-4 py-2.5 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-3 transition-colors"
                          >
                            <VideoIcon size={16} className="text-indigo-400" />
                            Fetch Video
                          </button>
                        )}
                        {!coverDownloaded && (
                          <button
                            onClick={() => onRefetch({ cover: true })}
                            className="w-full px-4 py-2.5 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-3 transition-colors"
                          >
                            <ImageIcon size={16} className="text-emerald-400" />
                            Fetch Cover
                          </button>
                        )}
                        {!musicDownloaded && (
                          <button
                            onClick={() => onRefetch({ music: true })}
                            className="w-full px-4 py-2.5 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-3 transition-colors"
                          >
                            <Music size={16} className="text-amber-400" />
                            Fetch Audio
                          </button>
                        )}
                        {(videoDownloaded && coverDownloaded && musicDownloaded) ? (
                          <>
                            <div className="px-4 py-2.5 text-sm text-zinc-500 flex items-center gap-3">
                              <Check size={16} className="text-emerald-400" />
                              All Fetched
                            </div>
                            <div className="border-t border-zinc-700 my-1" />
                            <button
                              onClick={() => onRefetch({ video: true, cover: true, music: true, force: true })}
                              className="w-full px-4 py-2.5 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-3 transition-colors"
                            >
                              <RefreshCw size={16} className="text-orange-400" />
                              Re-fetch All
                            </button>
                          </>
                        ) : (
                          <>
                            <div className="border-t border-zinc-700 my-1" />
                            <button
                              onClick={() => onRefetch({
                                video: !videoDownloaded,
                                cover: !coverDownloaded,
                                music: !musicDownloaded,
                              })}
                              className="w-full px-4 py-2.5 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-3 transition-colors"
                            >
                              <Download size={16} className="text-sky-400" />
                              Fetch All
                            </button>
                          </>
                        )}

                        {/* Delete section */}
                        {onDelete && (
                          <>
                            <div className="border-t border-zinc-700 my-1" />
                            <button
                              onClick={() => { setShowMoreMenu(false); setShowDeleteDialog(true); }}
                              className="w-full px-4 py-2.5 text-left text-sm text-red-400 hover:bg-red-950/50 hover:text-red-300 flex items-center gap-3 transition-colors"
                            >
                              <Trash2 size={16} />
                              Delete
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  </>
                )}
              </div>
              )}
            </div>
          </div>

          {/* Title */}
          <h2 className="text-2xl font-bold text-zinc-100 mb-3 leading-tight">
            {data.title || 'No Title'}
          </h2>

          {/* Time & Duration Row */}
          <div className="flex flex-col sm:flex-row sm:items-center gap-y-2 gap-x-6 mb-5 text-sm text-zinc-400">
             <div className="flex items-center gap-2">
                <Clock size={14} className="text-zinc-500"/>
                <span>Video Release Time: <span className="text-zinc-300 font-medium">{formatDateTime(data.published_at)}</span></span>
             </div>
             <div className="flex items-center gap-2">
                <Timer size={14} className="text-zinc-500"/>
                <span>Video Duration: <span className="text-zinc-300 font-medium">{formatDuration(data.duration)}</span></span>
             </div>
          </div>

          {/* Stats Grid */}
          <div className="grid grid-cols-4 gap-2 sm:gap-4 mb-6">
            <div className="flex flex-col items-center justify-center p-2 sm:p-3 bg-zinc-950 rounded-xl border border-zinc-800">
              <Heart className="w-4 h-4 sm:w-5 sm:h-5 text-rose-500 mb-1" />
              <span className="text-xs sm:text-sm font-bold text-white">{formatNumber(data.like_count)}</span>
              <span className="text-[9px] sm:text-[10px] text-zinc-500 uppercase tracking-wider mt-0.5">Likes</span>
            </div>
            <div className="flex flex-col items-center justify-center p-2 sm:p-3 bg-zinc-950 rounded-xl border border-zinc-800">
              <MessageCircle className="w-4 h-4 sm:w-5 sm:h-5 text-sky-500 mb-1" />
              <span className="text-xs sm:text-sm font-bold text-white">{formatNumber(data.comment_count)}</span>
              <span className="text-[9px] sm:text-[10px] text-zinc-500 uppercase tracking-wider mt-0.5">Comments</span>
            </div>
            <button
              onClick={() => {
                if (data.original_url) {
                  navigator.clipboard.writeText(data.original_url);
                  setCopiedShare(true);
                  setTimeout(() => setCopiedShare(false), 2000);
                }
              }}
              className="flex flex-col items-center justify-center p-2 sm:p-3 bg-zinc-950 rounded-xl border border-zinc-800 hover:border-emerald-500/50 hover:bg-emerald-500/5 transition-all cursor-pointer group"
              title="Click to copy link"
            >
              {copiedShare ? (
                <Check className="w-4 h-4 sm:w-5 sm:h-5 text-emerald-400 mb-1" />
              ) : (
                <Share2 className="w-4 h-4 sm:w-5 sm:h-5 text-emerald-500 mb-1 group-hover:scale-110 transition-transform" />
              )}
              <span className="text-xs sm:text-sm font-bold text-white">{copiedShare ? 'Copied!' : formatNumber(data.share_count)}</span>
              <span className="text-[9px] sm:text-[10px] text-zinc-500 uppercase tracking-wider mt-0.5">{copiedShare ? 'Link' : 'Shares'}</span>
            </button>
            <div className="flex flex-col items-center justify-center p-2 sm:p-3 bg-zinc-950 rounded-xl border border-zinc-800">
              <Bookmark className="w-4 h-4 sm:w-5 sm:h-5 text-amber-500 mb-1" />
              <span className="text-xs sm:text-sm font-bold text-white">{formatNumber(data.favorite_count)}</span>
              <span className="text-[9px] sm:text-[10px] text-zinc-500 uppercase tracking-wider mt-0.5">Collects</span>
            </div>
          </div>

          {/* Rating & Notes (from resources table) */}
          {onRatingChange && (
            <div className="mb-4 flex items-center gap-4">
              <span className="text-xs text-zinc-500 uppercase tracking-wider">Rating</span>
              <div className="flex items-center gap-0.5">
                {[1, 2, 3, 4, 5].map((star) => (
                  <button
                    key={star}
                    className="p-0 transition-colors"
                    onClick={() => onRatingChange(star === resourceRating ? 0 : star)}
                  >
                    <Star
                      size={16}
                      className={(resourceRating || 0) >= star ? 'text-amber-400 fill-amber-400' : 'text-zinc-600'}
                    />
                  </button>
                ))}
              </div>
            </div>
          )}
          {onNotesChange && (
            <div className="mb-4">
              <textarea
                value={resourceNotes || ''}
                onChange={(e) => onNotesChange(e.target.value)}
                onBlur={onNotesBlur}
                placeholder="Add notes..."
                rows={2}
                className="w-full bg-zinc-800/50 border border-zinc-700/50 rounded-lg px-3 py-2 text-sm text-zinc-300 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50 resize-none"
              />
            </div>
          )}

          {/* Tags - editable via UnifiedTagPicker */}
          {resourceId && (
            <div className="mb-4 -mx-4">
              <UnifiedTagPicker
                assignedTags={resourceTags.map(item => item.tag).filter((t): t is import('../types').Tag => !!t)}
                allTags={allTags}
                onAdd={handleAddTag}
                onRemove={handleRemoveTag}
                onCreate={handleCreateTag}
              />
            </div>
          )}

          {/* Platform hashtags (read-only, for analytics) */}
          {data.hashtags && (
            <div className="mb-4">
              <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
                Platform Tags
              </h4>
              <div className="flex flex-wrap gap-2">
                {data.hashtags.split(/\s+/).filter(h => h.startsWith('#') && h.length > 1).map((ht, i) => (
                  <span key={i} className="px-2.5 py-1 rounded-full border text-xs font-medium bg-zinc-800/30 text-zinc-500 border-zinc-700/50">
                    {ht}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* AI Status */}
          <div className="mb-4 flex items-center justify-end">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1" title={`Transcript: ${data.transcript_status || 'pending'}`}>
                <FileText size={14} className={getAIStatusClass(data.transcript_status)} />
              </div>
              <div className="flex items-center gap-1" title={`Summary: ${data.summary_status || 'pending'}`}>
                <Sparkles size={14} className={getAIStatusClass(data.summary_status)} />
              </div>
              <div className="flex items-center gap-1" title={`Visual Analysis: ${data.visual_analysis_status || 'pending'}`}>
                <Eye size={14} className={getAIStatusClass(data.visual_analysis_status)} />
              </div>
            </div>
          </div>

          {/* Action Buttons Row */}
          <div className="grid grid-cols-4 gap-1.5 sm:gap-2 mb-5">
             <button
               onClick={(e) => handleAction(e, 'copy')}
               className="flex items-center justify-center gap-1 sm:gap-2 p-2 sm:p-2.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors border border-zinc-700 hover:border-zinc-600"
             >
               {copied ? <Check size={16} className="text-green-500 shrink-0" /> : <Copy size={16} className="shrink-0" />}
               <span className="text-[10px] sm:text-xs font-medium truncate">{copied ? 'Copied' : 'Copy'}</span>
             </button>

             {/* AI Extract Button */}
             <button
               onClick={(e) => handleAction(e, 'extract')}
               disabled={loadingAction === 'extract'}
               className="ai-btn ai-btn-extract flex items-center justify-center gap-1 sm:gap-2 p-2 sm:p-2.5 rounded-lg text-teal-300 hover:text-teal-100"
             >
               {loadingAction === 'extract' ? <Loader2 size={16} className="animate-spin relative z-10 shrink-0" /> : <FileText size={16} className="relative z-10 shrink-0" />}
               <span className="ai-text text-[10px] sm:text-xs relative z-10 truncate">Extract</span>
             </button>

             {/* AI Rewrite Button */}
             <button
               onClick={(e) => handleAction(e, 'rewrite')}
               disabled={loadingAction === 'rewrite'}
               className="ai-btn ai-btn-rewrite flex items-center justify-center gap-1 sm:gap-2 p-2 sm:p-2.5 rounded-lg text-violet-300 hover:text-violet-100"
             >
               {loadingAction === 'rewrite' ? <Loader2 size={16} className="animate-spin relative z-10 shrink-0" /> : <PenTool size={16} className="relative z-10 shrink-0" />}
               <span className="ai-text text-[10px] sm:text-xs relative z-10 truncate">Rewrite</span>
             </button>

             {/* AI Analyze Button */}
             <button
               onClick={(e) => handleAction(e, 'analyze')}
               disabled={loadingAction === 'analyze'}
               className="ai-btn ai-btn-analyze flex items-center justify-center gap-1 sm:gap-2 p-2 sm:p-2.5 rounded-lg text-indigo-300 hover:text-indigo-100"
             >
               {loadingAction === 'analyze' ? <Loader2 size={16} className="animate-spin relative z-10 shrink-0" /> : <Wand2 size={16} className="relative z-10 shrink-0" />}
               <span className="ai-text text-[10px] sm:text-xs relative z-10 truncate">Analyze</span>
             </button>
          </div>

          {/* Description - No Background - Adaptive */}
          <div className="mb-4">
             <p className="text-zinc-300 text-sm whitespace-pre-wrap leading-relaxed">
               {data.description || <span className="text-zinc-500 italic">No description available.</span>}
             </p>
          </div>

          {/* AI Content Area - Adaptive */}
          <div className="space-y-4 mb-6 flex-grow">

            {/* Extract Result - Placed at top of AI results */}
            {extractText && (
                <div className="animate-in fade-in slide-in-from-top-2">
                    <div className="flex items-center gap-2 mb-2">
                        <div className="p-1 rounded bg-teal-500/10 text-teal-400">
                           <FileText size={12} />
                        </div>
                        <span className="text-xs font-semibold text-teal-200">Extracted Data</span>
                    </div>
                    <div className="p-3 rounded-lg border border-teal-500/20 bg-teal-500/5 text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
                        {extractText}
                    </div>
                </div>
            )}

            {/* Rewrite Result */}
            {rewrittenText && (
                <div className="animate-in fade-in slide-in-from-top-2">
                    <div className="flex items-center gap-2 mb-2">
                        <div className="p-1 rounded bg-indigo-500/10 text-indigo-400">
                           <PenTool size={12} />
                        </div>
                        <span className="text-xs font-semibold text-indigo-200">AI Rewrite</span>
                    </div>
                    <div className="p-3 rounded-lg border border-indigo-500/20 bg-indigo-500/5 text-sm text-zinc-300 leading-relaxed">
                        {rewrittenText}
                    </div>
                </div>
            )}

            {/* Analysis Result */}
            {analysisText && (
                <div className="animate-in fade-in slide-in-from-top-2">
                    <div className="flex items-center gap-2 mb-2">
                        <div className="p-1 rounded bg-purple-500/10 text-purple-400">
                           <Wand2 size={12} />
                        </div>
                        <span className="text-xs font-semibold text-purple-200">Content Analysis</span>
                    </div>
                    <div className="p-3 rounded-lg border border-purple-500/20 bg-purple-500/5 text-sm text-zinc-300 leading-relaxed">
                        {analysisText}
                    </div>
                </div>
            )}
         </div>

          {/* Actions Footer — only show when action handlers are provided */}
          {(onSave || onToggleCollection) && (
          <div className="space-y-3 pt-4 border-t border-zinc-800/50 mt-auto">
             <div className="flex gap-2">
                {onSave && (
                   <button
                    onClick={() => onSave(data)}
                    className="w-11 h-11 flex items-center justify-center bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg transition-colors border border-zinc-700"
                    title="Save to Library"
                   >
                     <Bookmark className="w-5 h-5" />
                   </button>
                )}
                {/* Add to Collection button */}
                {onToggleCollection && (
                  <div className="relative">
                    <button
                      onClick={() => setShowCollectionPicker(!showCollectionPicker)}
                      className={`w-11 h-11 flex items-center justify-center rounded-lg transition-colors border ${
                        videoCollectionIds.length > 0
                          ? 'bg-indigo-600/20 text-indigo-400 border-indigo-600/50 hover:bg-indigo-600/30'
                          : 'bg-zinc-800 text-zinc-300 border-zinc-700 hover:bg-zinc-700 hover:text-white'
                      }`}
                      title="Add to Collection"
                    >
                      <FolderPlus className="w-5 h-5" />
                    </button>

                    {/* Collection Picker */}
                    <CollectionPicker
                      isOpen={showCollectionPicker}
                      onClose={() => setShowCollectionPicker(false)}
                      collections={collections.map(c => ({
                        id: c.id,
                        name: c.name,
                        isShared: !!c.team_id,
                        videoCount: c.video_count || 0
                      }))}
                      selectedIds={videoCollectionIds}
                      onToggle={onToggleCollection}
                      onCreate={(name) => {
                        if (onCreateCollection) {
                          onCreateCollection(name, null);
                        }
                      }}
                    />
                  </div>
                )}

             </div>

             {!hidePreview && data.music_download_status && data.music_download_status !== 'skipped' && data.music_download_status !== 'SKIPPED' && (
               <button
                 onClick={onDownloadAudio}
                 className="w-full flex items-between justify-between px-4 py-3 bg-zinc-950 rounded-lg border border-zinc-800 text-xs text-zinc-400 hover:text-zinc-200 hover:border-zinc-700 transition-colors"
               >
                 <div className="flex items-center gap-3">
                   <div className="p-1.5 bg-indigo-500/10 rounded-md">
                      <Music className="w-3.5 h-3.5 text-indigo-500" />
                   </div>
                   <span className="truncate max-w-[250px] font-medium">{data.music_name || 'Original Audio'}</span>
                 </div>
                 <Download className="w-3.5 h-3.5" />
               </button>
             )}
          </div>
          )}
        </div>
      </div>

      {/* Move to Trash Dialog */}
      {showDeleteDialog && (
        <div
          className="fixed inset-0 z-[100] bg-black/80 backdrop-blur-sm animate-in fade-in duration-200"
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setShowDeleteDialog(false)}
        >
          <div
            className="bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden animate-in zoom-in-95 duration-200"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-amber-500/10 rounded-lg">
                  <Trash2 className="w-5 h-5 text-amber-500" />
                </div>
                <h3 className="text-lg font-semibold text-white">Move to Trash</h3>
              </div>
              <button
                onClick={() => setShowDeleteDialog(false)}
                className="p-1.5 text-zinc-500 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
              >
                <X size={18} />
              </button>
            </div>

            {/* Content */}
            <div className="px-5 py-4 space-y-4">
              <p className="text-sm text-zinc-400">
                This item will be moved to the Recycle Bin. You can restore it later.
              </p>

              {/* Media Preview */}
              <div className="flex items-center gap-3 p-3 bg-zinc-800/50 rounded-lg border border-zinc-700/50">
                <img
                  src={coverUrl || "https://picsum.photos/80/80"}
                  alt="Preview"
                  className="w-12 h-12 rounded-lg object-cover"
                  referrerPolicy="no-referrer"
                />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-white font-medium truncate">
                    {data.title || 'Untitled'}
                  </p>
                  <p className="text-xs text-zinc-500">@{data.author}</p>
                </div>
              </div>
            </div>

            {/* Footer */}
            <div className="flex gap-3 px-5 py-4 bg-zinc-800/30 border-t border-zinc-800">
              <button
                onClick={() => setShowDeleteDialog(false)}
                className="flex-1 px-4 py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg font-medium transition-colors border border-zinc-700"
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                disabled={isDeleting}
                className="flex-1 px-4 py-2.5 bg-amber-600 hover:bg-amber-500 text-white rounded-lg font-medium transition-colors disabled:opacity-70 flex items-center justify-center gap-2"
              >
                {isDeleting ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Moving...
                  </>
                ) : (
                  <>
                    <Trash2 className="w-4 h-4" />
                    Move to Trash
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
