import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useSearchParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft, Loader2, FileQuestion, UserRound,
  Share2, Download, MoreHorizontal, ExternalLink, Copy,
  Video as VideoIcon, Image as ImageIcon, Music, CloudDownload,
} from 'lucide-react';
import { Video } from '../types';
import { VideoPlayer } from '../components/VideoPlayer';
import { VideoDetailPanel } from '../components/VideoDetailPanel';
import { fetchVideoByDisplayId, updateItem, deleteItem, getDownloadUrl, getCoverDownloadUrl, getMusicDownloadUrl } from '../services/dataService';
import { getVideoUrl, isVideoType } from '../utils/awemeType';
import { downloadFile, downloadWithAuth } from '../utils/download';
import { parseShareLink } from '../services/parserService';
import { useToast } from '../components/Toast';

const MIN_PANEL_WIDTH = 380;
const MAX_PANEL_WIDTH = 800;
const DEFAULT_PANEL_WIDTH = 560;

export function PlayerPage() {
  const { displayId, teamId } = useParams<{ displayId: string; teamId: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const from = searchParams.get('from');

  const [video, setVideo] = useState<Video | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const playerRef = useRef<HTMLVideoElement | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [panelWidth, setPanelWidth] = useState(DEFAULT_PANEL_WIDTH);
  const [showDownloadMenu, setShowDownloadMenu] = useState(false);
  const [showMoreMenu, setShowMoreMenu] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [isFetching, setIsFetching] = useState(false);
  const { addToast } = useToast();
  const isDragging = useRef(false);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(0);

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
    if (!displayId) return;

    setIsLoading(true);
    setNotFound(false);

    fetchVideoByDisplayId(displayId).then((data) => {
      if (data) {
        setVideo(data);
      } else {
        setNotFound(true);
      }
      setIsLoading(false);
    });
  }, [displayId]);

  // Toolbar download handler — only downloads from backend server (no CDN fallback)
  const handleToolbarDownload = async (type: 'video' | 'cover' | 'audio' | 'images') => {
    if (!video?.platform_id) return;
    setShowDownloadMenu(false);
    setIsDownloading(true);
    const onSuccess = (f: string) => addToast(`Downloaded: ${f}`, 'success');
    try {
      let ok = false;
      if (type === 'video') {
        ok = await downloadWithAuth(getDownloadUrl(video.platform_id), `${video.platform_id}.mp4`, { onSuccess });
        if (!ok) addToast('Video file not available for download', 'error');
      } else if (type === 'cover') {
        ok = await downloadWithAuth(getCoverDownloadUrl(video.platform_id), `${video.platform_id}_cover.jpg`, { onSuccess });
        if (!ok) addToast('Cover file not available for download', 'error');
      } else if (type === 'audio') {
        ok = await downloadWithAuth(getMusicDownloadUrl(video.platform_id), `${video.platform_id}_audio.mp3`, { onSuccess });
        if (!ok) addToast('Audio file not available for download', 'error');
      } else if (type === 'images') {
        const urls = video.image_download_urls?.filter(u => u && u !== '#');
        if (urls?.length) {
          urls.forEach((url, idx) => {
            setTimeout(() => downloadFile(url, `${video.platform_id || 'image'}_${idx + 1}.jpg`, { onSuccess }), idx * 500);
          });
        } else {
          addToast('No images available for download', 'error');
        }
      }
    } finally {
      setIsDownloading(false);
    }
  };

  const handleFetchMedia = async (options: { video?: boolean; music?: boolean; cover?: boolean }) => {
    if (!video?.original_url) {
      addToast('No original URL available for fetch', 'error');
      return;
    }
    setShowDownloadMenu(false);
    setIsFetching(true);
    try {
      await parseShareLink(video.original_url, {
        video_bool: !!options.video,
        music_bool: !!options.music,
        cover_bool: !!options.cover,
      });
      const items = [
        options.video && 'Video',
        options.cover && 'Cover',
        options.music && 'Audio',
      ].filter(Boolean);
      addToast(`Fetch submitted: ${items.join(', ')}. Refreshing...`, 'success');
      // Auto-reload video data after a short delay so download status reflects the fetch
      setTimeout(async () => {
        if (displayId) {
          const data = await fetchVideoByDisplayId(displayId);
          if (data) setVideo(data);
        }
      }, 3000);
    } catch (error) {
      console.error('Fetch error:', error);
      addToast(error instanceof Error ? error.message : 'Fetch failed', 'error');
    } finally {
      setIsFetching(false);
    }
  };

  const handleBack = () => {
    if (from === 'downloads' && teamId) {
      navigate(`/t/${teamId}/resources/downloads`);
    } else {
      navigate(-1);
    }
  };

  const handleUpdate = async (id: string, updates: Partial<Video>) => {
    const updated = await updateItem(id, updates);
    setVideo((prev) => (prev ? { ...prev, ...updated } : prev));
  };

  const handleDelete = async (id: string, deleteFiles: boolean) => {
    await deleteItem(id, deleteFiles);
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
      <div className="flex flex-col h-full p-4 md:p-0">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800 mb-2 shrink-0">
          {/* Left: back + title */}
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <button
              onClick={handleBack}
              className="flex items-center gap-1.5 text-sm text-zinc-400 hover:text-zinc-200 transition-colors shrink-0"
            >
              <ArrowLeft size={16} />
              <span>Back</span>
            </button>
            <span className="text-sm text-zinc-200 font-medium truncate max-w-[400px]">
              {video.title || video.description || 'Media Player'}
            </span>
          </div>

          {/* Right: share + download + more */}
          <div className="flex items-center gap-1.5 shrink-0">
            {video.original_url && (
              <button
                onClick={() => {
                  navigator.clipboard.writeText(video.original_url);
                  addToast('Link copied to clipboard', 'success');
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-purple-600 hover:bg-purple-500 text-white rounded-lg transition-colors"
              >
                <Share2 size={14} />
                <span>Share</span>
              </button>
            )}

            {/* Download dropdown */}
            <div className="relative">
              <button
                onClick={() => setShowDownloadMenu(!showDownloadMenu)}
                disabled={isDownloading || isFetching}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors disabled:opacity-70"
              >
                {(isDownloading || isFetching) ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                <span>Download</span>
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

                      const videoStatus = video.video_download_status;
                      const coverStatus = video.cover_download_status;
                      const audioStatus = video.music_download_status;

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
                          {/* Audio */}
                          {isCompleted(audioStatus) ? (
                            <button onClick={() => handleToolbarDownload('audio')} className={btnClass}>
                              <Music size={13} className="text-amber-400" /> Audio
                            </button>
                          ) : isPending(audioStatus) ? (
                            <button disabled className={disabledClass}>
                              <Loader2 size={13} className="text-amber-400 animate-spin" /> Audio Downloading...
                            </button>
                          ) : isFailed(audioStatus) ? (
                            <button onClick={() => handleFetchMedia({ music: true })} className={btnClass}>
                              <CloudDownload size={13} className="text-red-400" /> Retry Audio
                            </button>
                          ) : video.original_url ? (
                            <button onClick={() => handleFetchMedia({ music: true })} className={btnClass}>
                              <CloudDownload size={13} className="text-amber-400" /> Fetch Audio
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
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
        <div className="flex-1 min-h-0 flex">
          {/* Video Player — main area */}
          <div className="flex-1 min-w-0 relative">
            {getVideoUrl(video) ? (
              <VideoPlayer
                src={getVideoUrl(video)!}
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
          {/* Resize handle */}
          <div
            onMouseDown={handleResizeStart}
            className="w-1 shrink-0 cursor-col-resize group relative mx-1.5"
          >
            <div className="absolute inset-y-0 -left-1 -right-1 group-hover:bg-blue-500/30 transition-colors rounded" />
          </div>
          {/* Detail Panel — right sidebar */}
          <div
            style={{ width: panelWidth }}
            className="shrink-0 overflow-y-auto custom-scrollbar"
          >
            <VideoDetailPanel
              video={video}
              onClose={handleBack}
              onUpdate={handleUpdate}
              onDelete={handleDelete}
              hidePreview
            />
          </div>
        </div>
      </div>
    </div>
  );
}
