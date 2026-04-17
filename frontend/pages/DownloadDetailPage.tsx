import React from 'react';
import {
  ArrowLeft, Loader2, FileQuestion, UserRound,
  Share2, Download, MoreHorizontal, ExternalLink, Copy, Trash2,
  Video as VideoIcon,
} from 'lucide-react';
import { VideoPlayer } from '../components/VideoPlayer';
import { SlidePlayer } from '../components/SlidePlayer';
import { VideoDetailPanel } from '../components/VideoDetailPanel';
import { ShareModal } from '../components/ShareModal';
import { getDownloadUrl, getCoverDownloadUrl, getMusicDownloadUrl } from '../services/dataService';
import { getVideoUrl, isAlbumType } from '../utils/awemeType';
import { downloadFile, downloadWithAuth } from '../utils/download';
import { fetchMediaByType, extractAudio } from '../services/parserService';
import { useDownloadDetail } from './DownloadDetailPage/useDownloadDetail';
import { DownloadMenuDropdown, MobileDownloadMenu } from './DownloadDetailPage/DownloadMenuDropdown';
import { DeleteDialog } from './DownloadDetailPage/DeleteDialog';

export { useDownloadDetail } from './DownloadDetailPage/useDownloadDetail';
export { DownloadMenuDropdown, MobileDownloadMenu } from './DownloadDetailPage/DownloadMenuDropdown';
export { DeleteDialog } from './DownloadDetailPage/DeleteDialog';

interface DownloadDetailPageProps {
  resourceId?: string;
  mediaId?: string;
}

export function DownloadDetailPage({ resourceId: propResourceId, mediaId: propMediaId }: DownloadDetailPageProps = {}) {
  const detail = useDownloadDetail({ propResourceId, propMediaId });
  const {
    video, isLoading, notFound, playerRef,
    resourceId, resourceRating, resourceNotes, hlsUrl, authToken, mediaToken,
    panelWidth, showDownloadMenu, showMoreMenu, isShareModalOpen, showDeleteDialog,
    isDeleting, isDownloading, isFetching, isMobile,
    setShowDownloadMenu, setShowMoreMenu, setIsShareModalOpen, setShowDeleteDialog,
    setIsDeleting, setIsDownloading, setIsFetching,
    handleTimeUpdate, handleDurationChange, handleResizeStart,
    handleRatingChange, handleNotesChange, handleNotesBlur,
    handleUpdate, handleDelete, handleBack, addToast,
  } = detail;

  // Toolbar download handler — only downloads from backend server (no CDN fallback)
  const handleToolbarDownload = async (type: 'video' | 'cover' | 'audio' | 'images') => {
    if (!video?.platform_id) return;
    setShowDownloadMenu(false);
    setShowMoreMenu(false);
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
    <div className="animate-in fade-in slide-in-from-bottom-4 duration-500 h-full flex flex-col pt-[env(safe-area-inset-top)] sm:pt-0">
      {/* Mobile: floating back button overlaying content */}
      <button
        onClick={handleBack}
        className="sm:hidden fixed left-3 z-40 p-2 bg-black/20 backdrop-blur-md rounded-full text-white hover:bg-black/40 transition-colors shadow-lg border border-white/5"
        style={{ top: 'calc(env(safe-area-inset-top) + 0.625rem)' }}
      >
        <ArrowLeft size={20} className="drop-shadow-md" />
      </button>

      <div className="flex flex-col h-full p-0 sm:p-4 md:p-0">
        {/* Desktop header only */}
        <div className="hidden sm:flex items-center justify-between px-4 py-2.5 border-b border-zinc-800 mb-2 shrink-0">
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
            {resourceId && (
              <button
                onClick={() => setIsShareModalOpen(true)}
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
                <DownloadMenuDropdown
                  video={video}
                  isDownloading={isDownloading}
                  isFetching={isFetching}
                  onClose={() => setShowDownloadMenu(false)}
                  onDownload={handleToolbarDownload}
                  onFetchMedia={handleFetchMedia}
                  onExtractAudio={handleExtractAudio}
                />
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
          <div className="w-full aspect-video sm:h-[50vh] sm:aspect-auto md:h-auto md:flex-1 md:min-w-0 relative shrink-0 md:shrink bg-black">
            {video.media_type && isAlbumType(video.media_type) ? (
              <SlidePlayer mediaId={String(video.id)} mediaToken={mediaToken ?? undefined} downloadStatus={video.image_download_status || video.video_download_status || undefined} />
            ) : (hlsUrl || getVideoUrl(video, mediaToken ?? undefined)) ? (
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
                {video.video_download_status === 'downloading' || video.video_download_status === 'pending' ? (
                  <>
                    <Loader2 size={48} className="text-indigo-500 animate-spin" />
                    <p className="text-zinc-300 text-sm font-medium">Downloading...</p>
                    <p className="text-zinc-500 text-xs">The media file is being downloaded. Please wait.</p>
                  </>
                ) : (
                  <>
                    <VideoIcon size={48} className="text-zinc-600" />
                    <p className="text-zinc-400 text-sm font-medium">Video not available for streaming</p>
                  </>
                )}
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
                resourceId={resourceId || undefined}
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
                    {resourceId && (
                      <button
                        onClick={() => setIsShareModalOpen(true)}
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
                            <MobileDownloadMenu
                              video={video}
                              onClose={() => setShowMoreMenu(false)}
                              onDownload={handleToolbarDownload}
                              onFetchMedia={handleFetchMedia}
                            />
                            {video.original_url && (
                              <a
                                href={video.original_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors"
                                onClick={() => setShowMoreMenu(false)}
                              >
                                <ExternalLink size={13} /> Open Original
                              </a>
                            )}
                            <div className="border-t border-zinc-700 my-1" />
                            <button
                              className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-red-400 hover:bg-red-950/50 hover:text-red-300 transition-colors"
                              onClick={(e) => { e.stopPropagation(); setShowMoreMenu(false); setTimeout(() => setShowDeleteDialog(true), 50); }}
                            >
                              <Trash2 size={13} /> Delete
                            </button>
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
        <DeleteDialog
          video={video}
          isDeleting={isDeleting}
          onClose={() => setShowDeleteDialog(false)}
          onConfirm={async () => {
            setIsDeleting(true);
            try {
              await handleDelete(video.id, false);
            } finally {
              setIsDeleting(false);
              setShowDeleteDialog(false);
            }
          }}
        />
      )}

      {/* Share Modal */}
      <ShareModal
        isOpen={isShareModalOpen}
        onClose={() => setIsShareModalOpen(false)}
        resourceId={resourceId || undefined}
        defaultName={video?.title || video?.description || 'Shared Media'}
      />
    </div>
  );
}
