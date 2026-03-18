import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useSearchParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft, Loader2, FileQuestion, UserRound,
  Share2, Download, MoreHorizontal, ExternalLink, Copy, Trash2, X,
  Video as VideoIcon, Image as ImageIcon, Music, CloudDownload,
} from 'lucide-react';
import { Video } from '../types';
import { VideoPlayer } from '../components/VideoPlayer';
import { VideoDetailPanel } from '../components/VideoDetailPanel';
import { fetchVideoByDisplayId, updateItem, deleteItem, getDownloadUrl, getCoverDownloadUrl, getMusicDownloadUrl } from '../services/dataService';
import { trashResourceByMediaId } from '../services/resourceService';
import { getVideoUrl, isVideoType } from '../utils/awemeType';
import { downloadFile, downloadWithAuth } from '../utils/download';
import { fetchMediaByType, extractAudio } from '../services/parserService';
import { updateResource, getVersionHlsUrl } from '../services/resourceService';
import { useToast } from '../components/Toast';
import { useAuth } from '../contexts/AuthContext';
import { getSupabaseClient, isSupabaseConfigured, getSupabaseAccessToken } from '../supabaseClient';

const MIN_PANEL_WIDTH = 380;
const MAX_PANEL_WIDTH = 800;
const DEFAULT_PANEL_WIDTH = 560;

interface PlayerPageProps {
  resourceId?: string;
  mediaId?: string;
}

export function PlayerPage({ resourceId: propResourceId, mediaId: propMediaId }: PlayerPageProps = {}) {
  const { displayId, teamId } = useParams<{ displayId: string; teamId: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const from = searchParams.get('from');

  // Support both: direct URL params (legacy /player/:displayId) and props (from ResourceDetailPage)
  const effectiveDisplayId = propMediaId || displayId;

  const [video, setVideo] = useState<Video | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const playerRef = useRef<HTMLVideoElement | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [panelWidth, setPanelWidth] = useState(DEFAULT_PANEL_WIDTH);
  const [showDownloadMenu, setShowDownloadMenu] = useState(false);
  const [showMoreMenu, setShowMoreMenu] = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [isFetching, setIsFetching] = useState(false);
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches
  );
  const { addToast } = useToast();
  const { mediaToken } = useAuth();
  const isDragging = useRef(false);

  // Track mobile breakpoint for responsive layout
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)');
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(0);

  // Resource-level data (from resources table, linked via media_id)
  const [resourceId, setResourceId] = useState<string | null>(null);
  const [resourceRating, setResourceRating] = useState(0);
  const [resourceNotes, setResourceNotes] = useState('');
  const [hlsUrl, setHlsUrl] = useState<string | null>(null);
  const [authToken, setAuthToken] = useState<string | null>(null);

  const handleTimeUpdate = useCallback((seconds: number) => {
    setCurrentTime(seconds);
  }, []);

  const handleDurationChange = useCallback((seconds: number) => {
    setDuration(seconds);
  }, []);

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isDragging.current = true;
    dragStartX.current = e.clientX;
    dragStartWidth.current = panelWidth;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const handleMouseMove = (ev: MouseEvent) => {
      if (!isDragging.current) return;
      const delta = dragStartX.current - ev.clientX;
      const newWidth = Math.min(MAX_PANEL_WIDTH, Math.max(MIN_PANEL_WIDTH, dragStartWidth.current + delta));
      setPanelWidth(newWidth);
    };

    const handleMouseUp = () => {
      isDragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
  }, [panelWidth]);

  useEffect(() => {
    if (!effectiveDisplayId) return;

    setIsLoading(true);
    setNotFound(false);

    fetchVideoByDisplayId(effectiveDisplayId).then((data) => {
      if (data) {
        setVideo(data);
      } else {
        setNotFound(true);
      }
      setIsLoading(false);
    });
  }, [effectiveDisplayId]);

  // Realtime: auto-refresh when this parsed_media record is updated (e.g. download completes)
  useEffect(() => {
    if (!video?.id) return;
    const supabase = getSupabaseClient();
    if (!isSupabaseConfigured() || !supabase) return;

    const channel = supabase
      .channel(`player_media_${video.id}`)
      .on(
        'postgres_changes',
        {
          event: 'UPDATE',
          schema: 'public',
          table: 'parsed_media',
          filter: `id=eq.${video.id}`,
        },
        (payload) => {
          console.log('[PlayerPage] Realtime update for media:', payload.new);
          setVideo((prev) => prev ? { ...prev, ...payload.new } as Video : prev);
        }
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, [video?.id]);

  // Fetch associated resource (rating/notes/HLS) from resources + resource_versions
  useEffect(() => {
    if (!video?.id) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    (async () => {
      // Step 1: Get the resource linked to this parsed_media
      const { data: resource } = await supabase
        .from('resources')
        .select('id, rating, notes, current_version, mime_type')
        .eq('media_id', video.id)
        .limit(1)
        .maybeSingle();

      if (!resource) return;

      const resId = String(resource.id);
      setResourceId(resId);
      setResourceRating(resource.rating || 0);
      setResourceNotes(resource.notes || '');

      // Step 2: Check if the current version has completed HLS transcoding
      // NOTE: Don't gate on resource.mime_type — it's often null.
      // Let the version's hls_path + transcode_status decide.
      const { data: versions } = await supabase
        .from('resource_versions')
        .select('id, hls_path, transcode_status, version_number')
        .eq('resource_id', resource.id)
        .eq('version_number', resource.current_version || 1)
        .limit(1)
        .maybeSingle();

      if (versions?.hls_path && versions.transcode_status === 'completed') {
        const token = await getSupabaseAccessToken();
        setAuthToken(token);
        setHlsUrl(getVersionHlsUrl(resId, String(versions.id), token || undefined));
      }
    })();
  }, [video?.id]);

  const handleRatingChange = useCallback(async (rating: number) => {
    if (!resourceId) return;
    setResourceRating(rating);
    try {
      await updateResource(resourceId, { rating });
    } catch (err) {
      console.error('Failed to update rating:', err);
    }
  }, [resourceId]);

  const handleNotesChange = useCallback((notes: string) => {
    setResourceNotes(notes);
  }, []);

  const handleNotesBlur = useCallback(async () => {
    if (!resourceId) return;
    try {
      await updateResource(resourceId, { notes: resourceNotes || null });
    } catch (err) {
      console.error('Failed to update notes:', err);
    }
  }, [resourceId, resourceNotes]);

  // Toolbar download handler — only downloads from backend server (no CDN fallback)
  const handleToolbarDownload = async (type: 'video' | 'cover' | 'audio' | 'images') => {
    if (!video?.platform_id) return;
    setShowDownloadMenu(false);
    setIsDownloading(true);
    const onSuccess = (f: string) => addToast(`Downloaded: ${f}`, 'success');
    // Use title as filename base, fallback to platform_id
    const baseName = (video.title || video.platform_id || 'media')
      .replace(/[\\/:*?"<>|]/g, '_')  // Remove filesystem-unsafe characters
      .trim()
      .slice(0, 100);  // Limit length
    try {
      let ok = false;
      if (type === 'video') {
        ok = await downloadWithAuth(getDownloadUrl(video.platform_id), `${baseName}.mp4`, { onSuccess });
        if (!ok) addToast('Video file not available for download', 'error');
      } else if (type === 'cover') {
        ok = await downloadWithAuth(getCoverDownloadUrl(video.platform_id), `${baseName}_cover.jpg`, { onSuccess });
        if (!ok) addToast('Cover file not available for download', 'error');
      } else if (type === 'audio') {
        ok = await downloadWithAuth(getMusicDownloadUrl(video.platform_id), `${baseName}_audio.mp3`, { onSuccess });
        if (!ok) addToast('Audio file not available for download', 'error');
      } else if (type === 'images') {
        const urls = video.image_download_urls?.filter(u => u && u !== '#');
        if (urls?.length) {
          urls.forEach((url, idx) => {
            setTimeout(() => downloadFile(url, `${baseName}_${idx + 1}.jpg`, { onSuccess }), idx * 500);
          });
        } else {
          addToast('No images available for download', 'error');
        }
      }
    } finally {
      setIsDownloading(false);
    }
  };

  const handleFetchMedia = async (options: { video?: boolean; cover?: boolean }) => {
    if (!video?.platform_id) {
      addToast('No platform ID available for fetch', 'error');
      return;
    }
    setShowDownloadMenu(false);
    setIsFetching(true);
    try {
      const types: string[] = [];
      if (options.video) types.push('video');
      if (options.cover) types.push('cover');
      const result = await fetchMediaByType(video.platform_id, types);
      const items = [
        options.video && 'Video',
        options.cover && 'Cover',
      ].filter(Boolean);
      const detail = result.types_skipped?.length
        ? ` (cached: ${result.types_skipped.join(', ')})`
        : '';
      addToast(`Fetch submitted: ${items.join(', ')}${detail}. Will update automatically.`, 'success');
    } catch (error) {
      console.error('Fetch error:', error);
      addToast(error instanceof Error ? error.message : 'Fetch failed', 'error');
    } finally {
      setIsFetching(false);
    }
  };

  const handleExtractAudio = async () => {
    if (!video?.platform_id) return;
    setShowDownloadMenu(false);
    setIsFetching(true);
    try {
      await extractAudio(video.platform_id);
      addToast('Audio extraction started. Will update automatically.', 'success');
    } catch (error) {
      console.error('Extract audio error:', error);
      addToast(error instanceof Error ? error.message : 'Audio extraction failed', 'error');
    } finally {
      setIsFetching(false);
    }
  };

  // When embedded from ResourceDetailPage, use browser back; otherwise navigate explicitly
  const isEmbedded = !!propResourceId;

  const handleBack = () => {
    navigate(-1);
  };

  const handleUpdate = async (id: string, updates: Partial<Video>) => {
    const updated = await updateItem(id, updates);
    setVideo((prev) => (prev ? { ...prev, ...updated } : prev));
  };

  const handleDelete = async (id: string, _deleteFiles: boolean) => {
    try {
      // from=downloads means personal library → trash globally (is_trashed=true)
      // Otherwise assume team context → only unlink from team
      if (from === 'downloads' || !teamId) {
        await trashResourceByMediaId(id);
      } else {
        await trashResourceByMediaId(id, 'team', teamId);
      }
    } catch (err) {
      console.error('Failed to trash resource:', err);
    }
    handleBack();
  };

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full min-h-[400px]">
        <Loader2 size={32} className="animate-spin text-zinc-500 mb-3" />
        <p className="text-zinc-500 text-sm">Loading...</p>
      </div>
    );
  }

  if (notFound || !video) {
    return (
      <div className="flex flex-col items-center justify-center h-full min-h-[400px] text-center">
        <FileQuestion size={48} className="text-zinc-600 mb-4" />
        <p className="text-zinc-400 text-lg font-medium mb-2">Video not found</p>
        <p className="text-zinc-500 text-sm mb-6">The video you're looking for doesn't exist or has been removed.</p>
        <button
          onClick={handleBack}
          className="flex items-center gap-2 px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors"
        >
          <ArrowLeft size={16} />
          Go Back
        </button>
      </div>
    );
  }

  return (
    <div className="animate-in fade-in slide-in-from-bottom-4 duration-500 h-full flex flex-col">
      <div className="flex flex-col h-full p-2 sm:p-4 md:p-0">
        <div className="hidden sm:flex items-center justify-between px-2 sm:px-4 py-2 sm:py-2.5 border-b border-zinc-800 mb-2 shrink-0">
          {/* Left: back + title */}
          <div className="flex items-center gap-1.5 sm:gap-2 min-w-0 flex-1">
            <button
              onClick={handleBack}
              className="flex items-center gap-1 sm:gap-1.5 text-sm text-zinc-400 hover:text-zinc-200 transition-colors shrink-0"
            >
              <ArrowLeft size={16} />
              <span className="hidden sm:inline">Back</span>
            </button>
            <span className="text-xs sm:text-sm text-zinc-200 font-medium truncate">
              {video.title || video.description || 'Media Player'}
            </span>
          </div>

          {/* Right: share + download + more — hidden on mobile, shown in metadata area instead */}
          <div className="hidden sm:flex items-center gap-1 sm:gap-1.5 shrink-0">
            {video.original_url && (
              <button
                onClick={() => {
                  navigator.clipboard.writeText(video.original_url);
                  addToast('Link copied to clipboard', 'success');
                }}
                className="flex items-center gap-1.5 px-2 sm:px-3 py-1.5 text-xs font-medium bg-purple-600 hover:bg-purple-500 text-white rounded-lg transition-colors"
              >
                <Share2 size={14} />
                <span className="hidden sm:inline">Share</span>
              </button>
            )}

            {/* Download dropdown */}
            <div className="relative">
              <button
                onClick={() => setShowDownloadMenu(!showDownloadMenu)}
                disabled={isDownloading || isFetching}
                className="flex items-center gap-1.5 px-2 sm:px-3 py-1.5 text-xs font-medium text-zinc-300 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors disabled:opacity-70"
              >
                {(isDownloading || isFetching) ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                <span className="hidden sm:inline">Download</span>
              </button>
              {showDownloadMenu && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setShowDownloadMenu(false)} />
                  <div className="absolute right-0 top-full mt-1 z-20 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl overflow-hidden min-w-[180px]">
                    {(() => {
                      // User-level download status (from resource, overlaid by backend detail endpoint)
                      const isCompleted = (s?: string) => s?.toLowerCase() === 'completed';
                      const isPending = (s?: string) => { const l = s?.toLowerCase(); return l === 'pending' || l === 'downloading'; };
                      const isFailed = (s?: string) => s?.toLowerCase() === 'failed';

                      // Defense: if status says "completed" but no actual file path, treat as unfetched
                      const hasVideoFile = !!(video.download_path || video.hls_path);
                      const hasCoverFile = !!video.cover_download_path;
                      const hasAudioFile = !!video.music_download_path;

                      const videoStatus = isCompleted(video.video_download_status) && !hasVideoFile ? undefined : video.video_download_status;
                      const coverStatus = isCompleted(video.cover_download_status) && !hasCoverFile ? undefined : video.cover_download_status;
                      const audioStatus = isCompleted(video.music_download_status) && !hasAudioFile ? undefined : video.music_download_status;

                      const btnClass = "w-full px-3 py-1.5 text-left text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2 transition-colors";
                      const disabledClass = "w-full px-3 py-1.5 text-left text-xs text-zinc-500 flex items-center gap-2 cursor-default";
                      return (
                        <div className="py-1">
                          {/* Video */}
                          {isVideoType(video.media_type) && (
                            isCompleted(videoStatus) ? (
                              <button onClick={() => handleToolbarDownload('video')} className={btnClass}>
                                <VideoIcon size={13} className="text-indigo-400" /> Video
                              </button>
                            ) : isPending(videoStatus) ? (
                              <button disabled className={disabledClass}>
                                <Loader2 size={13} className="text-indigo-400 animate-spin" /> Video Downloading...
                              </button>
                            ) : isFailed(videoStatus) ? (
                              <button onClick={() => handleFetchMedia({ video: true })} className={btnClass}>
                                <CloudDownload size={13} className="text-red-400" /> Retry Video
                              </button>
                            ) : video.original_url ? (
                              <button onClick={() => handleFetchMedia({ video: true })} className={btnClass}>
                                <CloudDownload size={13} className="text-indigo-400" /> Fetch Video
                              </button>
                            ) : null
                          )}
                          {/* Images (carousel/image content) */}
                          {video.image_download_urls && video.image_download_urls.length > 0 && (
                            <button onClick={() => handleToolbarDownload('images')} className={btnClass}>
                              <ImageIcon size={13} className="text-pink-400" /> Images ({video.image_download_urls.length})
                            </button>
                          )}
                          {/* Cover */}
                          {isCompleted(coverStatus) ? (
                            <button onClick={() => handleToolbarDownload('cover')} className={btnClass}>
                              <ImageIcon size={13} className="text-emerald-400" /> Cover
                            </button>
                          ) : isPending(coverStatus) ? (
                            <button disabled className={disabledClass}>
                              <Loader2 size={13} className="text-emerald-400 animate-spin" /> Cover Downloading...
                            </button>
                          ) : isFailed(coverStatus) ? (
                            <button onClick={() => handleFetchMedia({ cover: true })} className={btnClass}>
                              <CloudDownload size={13} className="text-red-400" /> Retry Cover
                            </button>
                          ) : video.original_url ? (
                            <button onClick={() => handleFetchMedia({ cover: true })} className={btnClass}>
                              <CloudDownload size={13} className="text-emerald-400" /> Fetch Cover
                            </button>
                          ) : null}
                          {/* Audio — auto-extracted from video, re-extract if missing */}
                          {isCompleted(audioStatus) ? (
                            <button onClick={() => handleToolbarDownload('audio')} className={btnClass}>
                              <Music size={13} className="text-amber-400" /> Audio
                            </button>
                          ) : isPending(audioStatus) ? (
                            <button disabled className={disabledClass}>
                              <Loader2 size={13} className="text-amber-400 animate-spin" /> Extracting Audio...
                            </button>
                          ) : hasVideoFile ? (
                            <button onClick={() => handleExtractAudio()} className={btnClass}>
                              <Music size={13} className="text-amber-400" /> Extract Audio
                            </button>
                          ) : null}
                        </div>
                      );
                    })()}
                  </div>
                </>
              )}
            </div>

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
                    {video.original_url && (
                      <a
                        href={video.original_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors"
                        onClick={() => setShowMoreMenu(false)}
                      >
                        <ExternalLink size={13} /> Open Original Link
                      </a>
                    )}
                    {video.original_url && (
                      <button
                        className="flex items-center gap-2 w-full text-left px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors"
                        onClick={() => {
                          setShowMoreMenu(false);
                          navigator.clipboard.writeText(video.original_url);
                          addToast('Link copied to clipboard', 'success');
                        }}
                      >
                        <Copy size={13} /> Copy Link
                      </button>
                    )}
                    <div className="border-t border-zinc-700 my-1" />
                    <button
                      className="flex items-center gap-2 w-full text-left px-3 py-1.5 text-xs text-red-400 hover:bg-red-950/50 hover:text-red-300 transition-colors"
                      onClick={() => {
                        setShowMoreMenu(false);
                        setShowDeleteDialog(true);
                      }}
                    >
                      <Trash2 size={13} /> Delete
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
        <div className="flex-1 min-h-0 flex flex-col md:flex-row overflow-y-auto md:overflow-y-hidden">
          {/* Video Player — main area */}
          <div className="w-full h-56 sm:h-64 md:h-auto md:flex-1 md:min-w-0 relative shrink-0 md:shrink">
            {(hlsUrl || getVideoUrl(video, mediaToken ?? undefined)) ? (
              <VideoPlayer
                src={hlsUrl || getVideoUrl(video, mediaToken ?? undefined)!}
                originalSrc={hlsUrl ? getVideoUrl(video, mediaToken ?? undefined) || undefined : undefined}
                authToken={authToken || undefined}
                playerRef={playerRef}
                onTimeUpdate={handleTimeUpdate}
                onDurationChange={handleDurationChange}
              />
            ) : (
              <div className="w-full h-full bg-black rounded-lg flex flex-col items-center justify-center gap-3">
                <VideoIcon size={48} className="text-zinc-600" />
                <p className="text-zinc-400 text-sm font-medium">Video not available for streaming</p>
                <p className="text-zinc-500 text-xs max-w-[300px] text-center">
                  This video hasn't been downloaded yet. Use the Download button to fetch the media file.
                </p>
                {video.original_url && (
                  <a
                    href={video.original_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-2 flex items-center gap-1.5 px-3 py-1.5 text-xs text-indigo-400 hover:text-indigo-300 bg-indigo-500/10 hover:bg-indigo-500/20 rounded-lg transition-colors"
                  >
                    <ExternalLink size={13} />
                    Open Original Link
                  </a>
                )}
              </div>
            )}
            {video.author && (
              <div className="absolute top-4 left-4 flex items-center gap-1.5 bg-black/50 backdrop-blur-sm rounded-full px-3 py-1.5 text-white/90 text-sm pointer-events-none">
                {video.source_platform && ['douyin', 'bilibili', 'youtube', 'tiktok', 'xiaohongshu', 'twitter'].includes(video.source_platform) ? (
                  <img src={`/icons/${video.source_platform}.svg`} alt="" className="w-4 h-4" />
                ) : (
                  <UserRound size={16} className="opacity-70" />
                )}
                <span>@{video.author}</span>
              </div>
            )}
          </div>
          {/* Resize handle — desktop only */}
          <div
            onMouseDown={handleResizeStart}
            className="hidden md:block w-1 shrink-0 cursor-col-resize group relative mx-1.5"
          >
            <div className="absolute inset-y-0 -left-1 -right-1 group-hover:bg-blue-500/30 transition-colors rounded" />
          </div>
          {/* Detail Panel — full width on mobile, resizable on desktop */}
          <div
            className="w-full md:w-auto shrink-0 overflow-y-auto custom-scrollbar"
            style={isMobile ? undefined : { width: panelWidth }}
          >
              <VideoDetailPanel
                video={video}
                onClose={handleBack}
                onUpdate={handleUpdate}
                onDelete={handleDelete}
                hidePreview
                resourceRating={resourceRating}
                resourceNotes={resourceNotes}
                onRatingChange={resourceId ? handleRatingChange : undefined}
                onNotesChange={resourceId ? handleNotesChange : undefined}
                onNotesBlur={resourceId ? handleNotesBlur : undefined}
                mobileActions={
                  <>
                    {video.original_url && (
                      <button
                        onClick={() => {
                          navigator.clipboard.writeText(video.original_url);
                          addToast('Link copied to clipboard', 'success');
                        }}
                        className="p-1.5 text-purple-400 hover:text-purple-300 hover:bg-purple-900/30 rounded-lg transition-colors"
                      >
                        <Share2 size={16} />
                      </button>
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
                          <div className="absolute right-0 top-full mt-1 z-20 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl py-1 w-48">
                            {(() => {
                              const isCompleted = (s?: string) => s?.toLowerCase() === 'completed';
                              const hasVideoFile = !!(video.download_path || video.hls_path);
                              const hasCoverFile = !!video.cover_download_path;
                              const hasAudioFile = !!video.music_download_path;
                              const btnClass = "flex items-center gap-2 w-full px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors";
                              return (
                                <>
                                  {isCompleted(video.video_download_status) && hasVideoFile && (
                                    <button onClick={() => { setShowMoreMenu(false); handleToolbarDownload('video'); }} className={btnClass}>
                                      <Download size={13} className="text-indigo-400" /> Download Video
                                    </button>
                                  )}
                                  {!isCompleted(video.video_download_status) && video.original_url && (
                                    <button onClick={() => { setShowMoreMenu(false); handleFetchMedia({ video: true }); }} className={btnClass}>
                                      <CloudDownload size={13} className="text-indigo-400" /> Fetch Video
                                    </button>
                                  )}
                                  {isCompleted(video.cover_download_status) && hasCoverFile && (
                                    <button onClick={() => { setShowMoreMenu(false); handleToolbarDownload('cover'); }} className={btnClass}>
                                      <Download size={13} className="text-emerald-400" /> Download Cover
                                    </button>
                                  )}
                                  {hasAudioFile && (
                                    <button onClick={() => { setShowMoreMenu(false); handleToolbarDownload('audio'); }} className={btnClass}>
                                      <Download size={13} className="text-amber-400" /> Download Audio
                                    </button>
                                  )}
                                  {video.original_url && (
                                    <a
                                      href={video.original_url}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className={btnClass}
                                      onClick={() => setShowMoreMenu(false)}
                                    >
                                      <ExternalLink size={13} /> Open Original
                                    </a>
                                  )}
                                  <div className="border-t border-zinc-700 my-1" />
                                  <button
                                    className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-red-400 hover:bg-red-950/50 hover:text-red-300 transition-colors"
                                    onClick={() => { setShowMoreMenu(false); setShowDeleteDialog(true); }}
                                  >
                                    <Trash2 size={13} /> Delete
                                  </button>
                                </>
                              );
                            })()}
                          </div>
                        </>
                      )}
                    </div>
                  </>
                }
              />
          </div>
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
            <div className="px-5 py-4 space-y-4">
              <p className="text-sm text-zinc-400">
                This item will be moved to the Recycle Bin. You can restore it later.
              </p>
              <div className="flex items-center gap-3 p-3 bg-zinc-800/50 rounded-lg border border-zinc-700/50">
                <img
                  src={(video.cover_urls?.[0]) || "https://picsum.photos/80/80"}
                  alt="Preview"
                  className="w-12 h-12 rounded-lg object-cover"
                  referrerPolicy="no-referrer"
                />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-white font-medium truncate">
                    {video.title || 'Untitled'}
                  </p>
                  <p className="text-xs text-zinc-500">@{video.author}</p>
                </div>
              </div>
            </div>
            <div className="flex gap-3 px-5 py-4 bg-zinc-800/30 border-t border-zinc-800">
              <button
                onClick={() => setShowDeleteDialog(false)}
                className="flex-1 px-4 py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg font-medium transition-colors border border-zinc-700"
              >
                Cancel
              </button>
              <button
                onClick={async () => {
                  setIsDeleting(true);
                  try {
                    await handleDelete(video.id, false);
                  } finally {
                    setIsDeleting(false);
                    setShowDeleteDialog(false);
                  }
                }}
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
}
