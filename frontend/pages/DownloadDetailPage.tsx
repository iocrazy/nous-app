import React, { useEffect, useRef, useState } from 'react';
import {
  ArrowLeft, Loader2, FileQuestion, UserRound,
  Share2, Download, MoreHorizontal, ExternalLink, Copy, Trash2,
  Video as VideoIcon, Music,
} from 'lucide-react';
import { VideoPlayer } from '../components/VideoPlayer';
import { SlidePlayer } from '../components/SlidePlayer';
import { AudioHero } from '../components/AudioHero';
import { VideoDetailPanel } from '../components/VideoDetailPanel';
import { MobileAudioScreen } from '../components/MobileAudioScreen';
import { ShareModal } from '../components/ShareModal';
import { AudioStageIsland } from '../components/AudioStageIsland';
import { PlaylistIsland } from '../components/PlaylistIsland';
import { AudioOverviewSide } from '../components/AudioOverviewSide';
import { AudioToolsMenuItems } from '../components/AudioToolsMenu';
import SodaLyricsTab from '../components/SodaLyricsTab';
import { AudioWaveformPlayer } from '../components/AudioWaveformPlayer';
import { extractCoverTint, tintFromTheme, type CoverTint } from '../utils/coverTint';
import { getDownloadUrl, getCoverDownloadUrl, getMusicDownloadUrl, getGalleryZipUrl } from '../services/dataService';
import { getVideoUrl, getCoverUrl, isAlbumType, isAudioType } from '../utils/awemeType';
import { buildSodaTheme } from '../utils/sodaTheme';
import { getApiUrl } from '../utils/apiConfig';
import { downloadFile, downloadWithAuth } from '../utils/download';
import { fetchMediaByType, extractAudio, downloadSodaTracks } from '../services/parserService';
import { sodaTrackId } from './DownloadDetailPage/sodaTrackId';
import { useDownloadDetail } from './DownloadDetailPage/useDownloadDetail';
import { DownloadMenuDropdown, MobileDownloadMenu } from './DownloadDetailPage/DownloadMenuDropdown';
import { DeleteDialog } from './DownloadDetailPage/DeleteDialog';
import { createPortal } from 'react-dom';
import { islandUI } from '../utils/featureFlags';
import { useIslandWork } from '../contexts/IslandWorkContext';
import { CometBack } from '../components/CometBack';

export { useDownloadDetail } from './DownloadDetailPage/useDownloadDetail';
export { DownloadMenuDropdown, MobileDownloadMenu } from './DownloadDetailPage/DownloadMenuDropdown';
export { DeleteDialog } from './DownloadDetailPage/DeleteDialog';

interface DownloadDetailPageProps {
  resourceId?: string;
  mediaId?: string;
  /** Pre-fetched ParsedMedia from the navigating card so the page can
   *  paint a skeleton immediately while the full fetch resolves. */
  preloaded?: import('../types').Video;
}

export function DownloadDetailPage({ resourceId: propResourceId, mediaId: propMediaId, preloaded }: DownloadDetailPageProps = {}) {
  const detail = useDownloadDetail({ propResourceId, propMediaId, preloaded });
  const {
    video, isLoading, notFound, playerRef, currentTime,
    resourceId, resourceRating, resourceNotes, hlsUrl, authToken, mediaToken,
    panelWidth, showDownloadMenu, showMoreMenu, isShareModalOpen, showDeleteDialog,
    isDeleting, isDownloading, isFetching, isMobile,
    setShowDownloadMenu, setShowMoreMenu, setIsShareModalOpen, setShowDeleteDialog,
    setIsDeleting, setIsDownloading, setIsFetching,
    handleTimeUpdate, handleDurationChange, handleResizeStart,
    handleRatingChange, handleNotesChange, handleNotesBlur,
    handleUpdate, handleDelete, handleBack, addToast,
  } = detail;

  // Island app-shell integration (no-op in classic — useIslandWork returns an
  // inert shape outside a shell, and `island` gates every island-only branch).
  const island = islandUI();
  const { infoIslandEl, setInfoVisible, setInfoAvailable } = useIslandWork();
  // Island desktop layout: stage in the work island + panel portaled to the info
  // island. Single source for both gates so they can't diverge (IslandShell is
  // `hidden sm:flex` at 640px while `isMobile` is the md breakpoint at 767px — a
  // mismatch in the 640–767px band would double-mount VideoDetailPanel).
  const islandDesktop = island && !isMobile;

  // iOS WebKit (standalone PWA): safe-area insets / viewport can stay unsettled
  // until the first scroll, leaving the header tucked under the status bar until
  // the user scrolls. Force the same reflow on mount so it's correct from the
  // first paint instead of after a manual scroll.
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const kick = () => {
      const el = scrollRef.current;
      if (el) {
        // Reset the inner scroller to the top (it can carry over a previous
        // page's scrollTop) and nudge it to trigger an iOS reflow.
        el.scrollTop = 1;
        el.scrollTop = 0;
      }
      // Reset the document to the top too — navigating from a scrolled feed
      // leaves window.scrollY non-zero; scrolling back to 0 also kicks the
      // iOS safe-area/viewport recompute.
      window.scrollTo(0, 0);
      window.dispatchEvent(new Event('resize'));
    };
    const raf = requestAnimationFrame(kick);
    const t1 = window.setTimeout(kick, 80);
    const t2 = window.setTimeout(kick, 250);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(t1);
      window.clearTimeout(t2);
    };
  }, []);

  // Island mode: this page owns an info island (the detail panel). Mark it
  // available + visible on mount so the shell mounts the info aside (giving us a
  // portal target), and tear it down on unmount. No-op in classic.
  useEffect(() => {
    if (!island) return;
    setInfoAvailable(true);
    setInfoVisible(true);
    return () => { setInfoAvailable(false); setInfoVisible(false); };
  }, [island, setInfoAvailable, setInfoVisible]);

  // Audio palette + cover-tint. Computed before the early returns (video-null
  // safe) so the tint hooks below obey the rules of hooks. Soda tracks carry
  // their own palette; non-Soda audio (extracted) seeds a stable per-track
  // color from the id instead of flat gray.
  const isAudio = !!video && isAudioType(video.media_type);
  const sodaTheme = isAudio && video
    ? buildSodaTheme(video.metadata?.colors, String(video.id))
    : undefined;
  const audioCoverUrl = isAudio && video ? getCoverUrl(video, mediaToken ?? undefined) : undefined;

  // Cover-tint for the island audio stage (--tint / --tint-deep). Seeded from
  // the sodaTheme accent, then canvas-sampled from the real cover. Gated to
  // island desktop audio — no-op in classic / video / mobile.
  const [tint, setTint] = useState<CoverTint>(() => tintFromTheme(sodaTheme));
  useEffect(() => {
    if (!islandDesktop || !isAudio || !audioCoverUrl) return;
    let cancelled = false;
    extractCoverTint(audioCoverUrl, tintFromTheme(sodaTheme)).then((t) => {
      if (!cancelled) setTint(t);
    });
    return () => { cancelled = true; };
    // sodaTheme is rebuilt each render (new object); the effect only reads its
    // stable `accent` string via tintFromTheme, so key on that to avoid a
    // sample-every-render loop (worse during playback as currentTime ticks).
  }, [islandDesktop, isAudio, audioCoverUrl, sodaTheme?.accent]);

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
        // Packaged zip from backend — works for image carousels AND
        // galleries containing videos / 动图. Backend zips whatever is
        // on disk in the slides/ folder, so one click = one file.
        ok = await downloadWithAuth(getGalleryZipUrl(video.platform_id), `${baseName}_gallery.zip`, { onSuccess });
        if (!ok) addToast('Gallery not available for download', 'error');
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

  // qishui (Soda) audio can't be re-fetched via the douyin/yt-dlp generic path
  // (fetchMediaByType) — it has its own download endpoint. Re-download the
  // single track through the soda path so a missing-audio track isn't a dead end.
  const handleFetchSodaAudio = async () => {
    if (!video) return;
    const trackId = sodaTrackId(video);
    if (!trackId) {
      addToast('No track id available to re-fetch', 'error');
      return;
    }
    setShowDownloadMenu(false);
    setIsFetching(true);
    try {
      await downloadSodaTracks([{ id: trackId, kind: 'track' }], video.title || undefined);
      addToast('Fetch submitted: Audio. Will update automatically.', 'success');
    } catch (error) {
      console.error('Soda audio fetch error:', error);
      addToast(error instanceof Error ? error.message : 'Audio fetch failed', 'error');
    } finally {
      setIsFetching(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full min-h-[400px]">
        <Loader2 size={32} className="animate-spin text-ink-500 mb-3" />
        <p className="text-ink-500 text-sm">Loading...</p>
      </div>
    );
  }

  if (notFound || !video) {
    return (
      <div className="flex flex-col items-center justify-center h-full min-h-[400px] text-center">
        <FileQuestion size={48} className="text-ink-600 mb-4" />
        <p className="text-ink-400 text-lg font-medium mb-2">Video not found</p>
        <p className="text-ink-500 text-sm mb-6">The video you're looking for doesn't exist or has been removed.</p>
        <button
          onClick={handleBack}
          className="flex items-center gap-2 px-4 py-2 bg-ink-800 hover:bg-ink-700 text-ink-300 rounded-lg transition-colors"
        >
          <ArrowLeft size={16} />
          Go Back
        </button>
      </div>
    );
  }

  // Desktop action buttons (Share / Download / More) — extracted verbatim so the
  // classic header and the island stage-head render the EXACT same buttons,
  // handlers, and dropdowns (single source, no drift). Wrapper differs per host.
  const actionButtons = (
    <>
      {resourceId && (
        <button
          onClick={() => setIsShareModalOpen(true)}
          className={`flex items-center gap-1.5 px-2 sm:px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${islandDesktop && isAudio ? 'btn-tint-cover' : 'btn-tint-indigo'}`}
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
          className="flex items-center gap-1.5 px-2 sm:px-3 py-1.5 text-xs font-medium text-ink-300 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors disabled:opacity-70"
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
            onFetchSodaAudio={handleFetchSodaAudio}
          />
        )}
      </div>

      {/* More menu */}
      <div className="relative">
        <button
          onClick={() => setShowMoreMenu(!showMoreMenu)}
          className="p-1.5 text-ink-400 hover:text-ink-200 hover:bg-ink-800 rounded-lg transition-colors"
        >
          <MoreHorizontal size={16} />
        </button>
        {showMoreMenu && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setShowMoreMenu(false)} />
            <div className="absolute right-0 top-full mt-1 z-20 bg-ink-900 border border-ink-700 rounded-lg shadow-xl py-1 w-44">
              {/* Island audio only: fold the AI actions + Notes (formerly on the
                  cover-side MediaCard) into this shared More menu so the audio
                  stage-head matches the mock (back · title · Share/Download/…)
                  with no extra icon. Gated on islandDesktop && isAudio so the
                  classic + video stage-heads stay byte-identical (D12). */}
              {islandDesktop && isAudio && (
                <>
                  <AudioToolsMenuItems
                    video={video}
                    onUpdate={handleUpdate}
                    resourceNotes={resourceNotes}
                    onNotesChange={resourceId ? handleNotesChange : undefined}
                    onNotesBlur={resourceId ? handleNotesBlur : undefined}
                    addToast={addToast}
                  />
                  <div className="border-t border-ink-700 my-1" />
                </>
              )}
              {video.original_url && (
                <a
                  href={video.original_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-ink-400 hover:bg-ink-800 hover:text-ink-200 transition-colors"
                  onClick={() => setShowMoreMenu(false)}
                >
                  <ExternalLink size={13} /> Open Original Link
                </a>
              )}
              {video.original_url && (
                <button
                  className="flex items-center gap-2 w-full text-left px-3 py-1.5 text-xs text-ink-400 hover:bg-ink-800 hover:text-ink-200 transition-colors"
                  onClick={() => {
                    setShowMoreMenu(false);
                    navigator.clipboard.writeText(video.original_url);
                    addToast('Link copied to clipboard', 'success');
                  }}
                >
                  <Copy size={13} /> Copy Link
                </button>
              )}
              <div className="border-t border-ink-700 my-1" />
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
    </>
  );

  // Player switch (audio / album / video / fallback) — extracted so the classic
  // split-pane stage and the island work-island viewport mount the EXACT same
  // component + props. Players are never portaled (HLS/auth/keyboard unaffected).
  const playerSwitch = isAudio ? (
    (video.music_download_path || video.extract_audio_path) ? (
      <AudioHero
        src={`${getApiUrl()}/api/v1/media/${video.id}/audio${mediaToken ? `?token=${encodeURIComponent(mediaToken)}` : ''}`}
        title={video.music_name || video.title || 'Audio'}
        subtitle={video.author || undefined}
        coverUrl={audioCoverUrl}
        duration={Number(video.duration) || undefined}
        onTimeUpdate={handleTimeUpdate}
        mediaId={String(video.id)}
        currentTime={currentTime}
        chorusStartSec={
          typeof video.metadata?.chorus?.start === 'number'
            ? video.metadata.chorus.start / 1000
            : undefined
        }
        theme={sodaTheme}
        sourcePlatform={video.source_platform}
      />
    ) : (
      <div className="w-full h-full bg-black rounded-lg flex flex-col items-center justify-center gap-3">
        <Music size={48} className="text-ink-600" />
        <p className="text-ink-400 text-sm font-medium">Audio not available</p>
        <p className="text-ink-500 text-xs max-w-[300px] text-center">
          This audio hasn't been downloaded yet. Use the Download button to fetch the audio file.
        </p>
      </div>
    )
  ) : video.media_type && isAlbumType(video.media_type) ? (
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
          <p className="text-ink-300 text-sm font-medium">Downloading...</p>
          <p className="text-ink-500 text-xs">The media file is being downloaded. Please wait.</p>
        </>
      ) : (
        <>
          <VideoIcon size={48} className="text-ink-600" />
          <p className="text-ink-400 text-sm font-medium">Video not available for streaming</p>
        </>
      )}
      <p className="text-ink-500 text-xs max-w-[300px] text-center">
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
  );

  // Author overlay badge (classic stage only — island shows the author in the
  // stage-head instead, so the badge isn't rendered in the island viewport).
  const authorBadge = video.author && (
    <div className="absolute top-4 left-4 flex items-center gap-1.5 bg-black/50 backdrop-blur-sm rounded-full px-3 py-1.5 text-white/90 text-sm pointer-events-none">
      {video.source_platform && ['douyin', 'bilibili', 'youtube', 'tiktok', 'xiaohongshu', 'twitter'].includes(video.source_platform) ? (
        <img src={`/icons/${video.source_platform}.svg`} alt="" className="w-4 h-4" />
      ) : (
        <UserRound size={16} className="opacity-70" />
      )}
      <span>@{video.author}</span>
    </div>
  );

  // Title + author line used by both the classic header (title only) and the
  // island stage-head. Island adds an author sub-line with the platform icon.
  const titleText = video.title || video.description || 'Media Player';

  // Island-audio stage slots (cover-side Overview | lyrics | capsule player),
  // extracted as consts like actionButtons/detailPanel so the audio branch is a
  // flat <AudioStageIsland .../> call. Only rendered when islandDesktop && isAudio.
  // Cover-side Overview — faithful port of the audio mock `.side` stack
  // (cover → song → artist → stats → stars → tags). Replaces the bare MediaCard
  // the stage used to host so the cover-side matches the mock exactly; the AI
  // actions + notes MediaCard carried fold into the shared stage-head More ("…")
  // menu (AudioToolsMenuItems, audio-only) so nothing is lost (D12). The SAME
  // audioCoverUrl drives both the tint sample and this cover image.
  const audioCoverSide = (
    <AudioOverviewSide
      video={video}
      coverUrl={audioCoverUrl}
      title={video.music_name || video.title || 'Audio'}
      author={video.author || undefined}
      rating={resourceRating}
      onRatingChange={resourceId ? handleRatingChange : undefined}
    />
  );

  // SAME synced-lyrics component the Lyrics tab uses (fetch / sync / Fetch Lyrics
  // behavior identical), bare variant for the column.
  const audioLyrics = (
    <SodaLyricsTab
      variant="bare"
      mediaId={String(video.id)}
      currentTime={currentTime}
      theme={sodaTheme}
      sourcePlatform={video.source_platform}
    />
  );

  // Island audio capsule player — same AudioWaveformPlayer (src / filename /
  // duration / chorus / onTimeUpdate / theme) but the `capsule` layout (mock
  // `.player`): compact tint waveform row. island-only (this const is only fed
  // to AudioStageIsland's player slot, rendered when islandDesktop && isAudio),
  // so the classic AudioHero player is untouched. Falls back to the
  // not-available notice when the audio file isn't downloaded.
  const audioPlayer = (video.music_download_path || video.extract_audio_path) ? (
    <AudioWaveformPlayer
      layout="capsule"
      src={`${getApiUrl()}/api/v1/media/${video.id}/audio${mediaToken ? `?token=${encodeURIComponent(mediaToken)}` : ''}`}
      filename={video.music_name || video.title || 'Audio'}
      duration={Number(video.duration) || undefined}
      chorusStartSec={
        typeof video.metadata?.chorus?.start === 'number'
          ? video.metadata.chorus.start / 1000
          : undefined
      }
      onTimeUpdate={handleTimeUpdate}
      theme={sodaTheme}
    />
  ) : (
    <div className="flex flex-col items-center justify-center gap-1.5 py-3 text-center">
      <Music size={24} className="text-ink-500" />
      <p className="text-ink-300 text-sm font-medium">Audio not available</p>
      <p className="text-ink-500 text-xs max-w-[320px]">
        This audio hasn't been downloaded yet. Use the Download button to fetch the audio file.
      </p>
    </div>
  );

  // VideoDetailPanel props — shared verbatim between the classic split-pane
  // column and the island portal so the panel behaves identically in both.
  const detailPanel = (
    <VideoDetailPanel
      video={video}
      resourceId={resourceId || undefined}
      playerCurrentTime={currentTime}
      sodaTheme={sodaTheme}
      compact={isMobile && isAudio}
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
              className="p-1.5 text-ink-400 hover:text-ink-200 hover:bg-ink-800 rounded-lg transition-colors"
            >
              <MoreHorizontal size={16} />
            </button>
            {showMoreMenu && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setShowMoreMenu(false)} />
                <div className="absolute right-0 top-full mt-1 z-20 bg-ink-900 border border-ink-700 rounded-lg shadow-xl py-1 w-48">
                  <MobileDownloadMenu
                    video={video}
                    onClose={() => setShowMoreMenu(false)}
                    onDownload={handleToolbarDownload}
                    onFetchMedia={handleFetchMedia}
                    onFetchSodaAudio={handleFetchSodaAudio}
                  />
                  {video.original_url && (
                    <a
                      href={video.original_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-50 transition-colors"
                      onClick={() => setShowMoreMenu(false)}
                    >
                      <ExternalLink size={13} /> Open Original
                    </a>
                  )}
                  <div className="border-t border-ink-700 my-1" />
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
  );

  return (
    <div className="animate-in fade-in slide-in-from-bottom-4 duration-500 h-full flex flex-col relative">
      {/* Mobile: floating back button overlaying content. Positioned `absolute`
          relative to this (already status-bar-cleared) root rather than fixed +
          env(), so it doesn't depend on iOS re-resolving env(safe-area-inset-top)
          for a freshly-mounted route (which only happens after a scroll). The
          root sits below the status bar because AppLayout's persistent <main>
          carries pt-[env(safe-area-inset-top)]. */}
      {/* Mobile: top scrim (Douyin-style immersive header — no opaque bar). A
          gradient behind the floating controls keeps the back arrow / author
          legible over ANY media, and the controls are placed at the safe-area
          inset (with a floor) so they never depend on env() resolving on mount.
          The player itself stays full-bleed under it. */}
      <div
        aria-hidden="true"
        className="sm:hidden absolute inset-x-0 top-0 z-30 pointer-events-none bg-gradient-to-b from-black/55 via-black/25 to-transparent"
        style={{ height: 'calc(max(env(safe-area-inset-top), 10px) + 64px)' }}
      />
      <button
        onClick={handleBack}
        style={{ top: 'calc(max(env(safe-area-inset-top), 10px) + 6px)' }}
        className="sm:hidden absolute left-3 z-40 p-2 bg-black/30 backdrop-blur-md rounded-full text-white hover:bg-black/45 transition-colors shadow-lg border border-white/10"
      >
        <ArrowLeft size={20} className="drop-shadow-md" />
      </button>

      {/* Mobile-audio top bar: @author next to the back chevron (locked layout).
          Mobile-video keeps the author overlay badge on the player instead. */}
      {isMobile && isAudio && video.author && (
        <div
          style={{ top: 'calc(max(env(safe-area-inset-top), 10px) + 6px)' }}
          className="sm:hidden absolute left-14 z-40 h-9 flex items-center text-white/90 text-sm font-medium drop-shadow-md pointer-events-none truncate max-w-[60%]"
        >
          @{video.author}
        </div>
      )}

      <div className="flex flex-col h-full p-0 sm:p-4 md:p-0">
        {/* Desktop header — classic split-pane layout only; island uses the stage-head */}
        {!island && (
        <div className="detail-header-glow hidden sm:flex items-center justify-between px-4 py-2.5 mb-2 shrink-0">
          {/* Glowing accent line that dips to cradle the round Back button */}
          <svg
            className="detail-header-glow__line"
            preserveAspectRatio="xMinYMid meet"
            viewBox="0 0 1100 30"
            aria-hidden="true"
          >
            <defs>
              <linearGradient
                id="detailGlowGrad"
                x1="0"
                y1="0"
                x2="1100"
                y2="0"
                gradientUnits="userSpaceOnUse"
              >
                <stop offset="0" stopColor="rgba(139,92,246,0)" />
                <stop offset="0.034" stopColor="rgba(139,92,246,1)" />
                <stop offset="0.155" stopColor="rgba(139,92,246,0.55)" />
                <stop offset="0.273" stopColor="rgba(139,92,246,0.25)" />
                <stop offset="0.382" stopColor="rgba(139,92,246,0)" />
              </linearGradient>
            </defs>
            {/* Tapering ribbon: thick where it cradles the ~38px button (dip
                centered at x≈37), narrowing to a point by x≈420 (title end) so
                the line gets thinner and fades out toward the right. */}
            <path d="M0 5.2 H6 C22 5.2 24 19.2 37 19.2 C50 19.2 52 5.2 68 5.2 L420 6.6 L68 6.8 C52 6.8 50 20.8 37 20.8 C24 20.8 22 6.8 6 6.8 H0 Z" />
          </svg>

          {/* Left: back + title */}
          <div className="flex items-center gap-2 sm:gap-3 min-w-0 flex-1">
            <button
              onClick={handleBack}
              title="Back"
              aria-label="Back"
              className="detail-back-btn"
            >
              <ArrowLeft size={18} />
            </button>
            <span className="text-xs sm:text-sm text-ink-200 font-medium truncate">
              {titleText}
            </span>
          </div>

          {/* Right: share + download + more — hidden on mobile, shown in metadata area instead */}
          <div className="hidden sm:flex items-center gap-1 sm:gap-1.5 shrink-0">
            {actionButtons}
          </div>
        </div>
        )}

        {/* Island desktop: the stage fills the work island; the detail panel is
            portaled to the shell's info island (rendered below). Mobile + classic
            share the split-pane content row in the else branch (unchanged). */}
        {islandDesktop ? (isAudio ? (
          /* Island desktop AUDIO: cover-tinted two-column stage (cover-side
             Overview | synced lyrics) + bottom play capsule. The Playlist island
             is portaled into infoIslandEl below (instead of VideoDetailPanel).
             --tint / --tint-deep are set on a display:contents wrapper so the
             .audio-stage root inherits them (AudioStageIsland is pure layout). */
          <div
            className="contents"
            style={{ ['--tint']: tint.tint, ['--tint-deep']: tint.tintDeep } as React.CSSProperties}
          >
            <AudioStageIsland
              title={titleText}
              author={video.author || undefined}
              onBack={handleBack}
              actions={actionButtons}
              coverSide={audioCoverSide}
              lyrics={audioLyrics}
              player={audioPlayer}
            />
          </div>
        ) : (
          <div className="hidden sm:flex flex-col h-full min-h-0">
            <div className="stage-head flex items-center gap-3 px-4 py-3 border-b border-line relative">
              <CometBack onClick={handleBack} title="Back" />
              <div className="min-w-0 flex-1">
                <div className="text-sm text-ink-200 font-medium truncate">{titleText}</div>
                {video.author && (
                  <div className="flex items-center gap-1.5 text-xs text-ink-400 mt-0.5 truncate">
                    {video.source_platform && ['douyin', 'bilibili', 'youtube', 'tiktok', 'xiaohongshu', 'twitter'].includes(video.source_platform) ? (
                      <img src={`/icons/${video.source_platform}.svg`} alt="" className="w-3.5 h-3.5" />
                    ) : (
                      <UserRound size={14} className="opacity-70" />
                    )}
                    <span className="truncate">@{video.author}</span>
                  </div>
                )}
              </div>
              <span className="comet-trail" />
              <div className="ml-auto flex items-center gap-2">
                {actionButtons}
              </div>
            </div>
            <div className="flex-1 min-h-0 grid place-items-center bg-[#050507] overflow-hidden">
              {playerSwitch}
            </div>
          </div>
        )) : (
        <div ref={scrollRef} className="flex-1 min-h-0 flex flex-col md:flex-row overflow-y-auto md:overflow-y-hidden">
          {isMobile && isAudio ? (
            /* LOCKED mobile-audio layout — ONE continuous gradient surface.
               Replaces the player-column / detail-panel split for mobile audio
               only; desktop + mobile-video are untouched. */
            <MobileAudioScreen
                video={video}
                src={(video.music_download_path || video.extract_audio_path)
                  ? `${getApiUrl()}/api/v1/media/${video.id}/audio${mediaToken ? `?token=${encodeURIComponent(mediaToken)}` : ''}`
                  : ''}
                hasAudio={!!(video.music_download_path || video.extract_audio_path)}
                onFetchAudio={video.source_platform === 'qishui' ? handleFetchSodaAudio : undefined}
                coverUrl={audioCoverUrl}
                theme={sodaTheme}
                mediaId={String(video.id)}
                currentTime={currentTime}
                onTimeUpdate={handleTimeUpdate}
                chorusStartSec={
                  typeof video.metadata?.chorus?.start === 'number'
                    ? video.metadata.chorus.start / 1000
                    : undefined
                }
                resourceId={resourceId || undefined}
                resourceRating={resourceRating}
                resourceNotes={resourceNotes}
                onRatingChange={resourceId ? handleRatingChange : undefined}
                onNotesChange={resourceId ? handleNotesChange : undefined}
                onNotesBlur={resourceId ? handleNotesBlur : undefined}
                onDownloadAudio={() => handleToolbarDownload('audio')}
                onDownloadCover={() => handleToolbarDownload('cover')}
                onFetchCover={video.source_platform === 'qishui' ? handleFetchSodaAudio : undefined}
                onShare={() => setIsShareModalOpen(true)}
                onDelete={() => setShowDeleteDialog(true)}
                onCopyLink={video.original_url ? () => {
                  navigator.clipboard.writeText(video.original_url);
                  addToast('Link copied to clipboard', 'success');
                } : undefined}
                canShare={!!resourceId}
              />
          ) : (
          <>
          {/* Video Player — main area */}
          <div
            className={`w-full ${isAudio ? 'min-h-[78vh]' : 'aspect-video'} sm:h-[50vh] sm:aspect-auto md:h-auto md:flex-1 md:min-w-0 relative shrink-0 md:shrink ${isAudio ? '' : 'bg-black'}`}
          >
            {playerSwitch}
            {authorBadge}
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
            {/* Mobile-audio is handled by the MobileAudioScreen short-circuit
                above, so this column only ever renders for desktop or
                mobile-video. */}
            {detailPanel}
          </div>
          </>
          )}
        </div>
        )}
      </div>

      {/* Island: portal the detail panel into the shell's info island. The
          players stay in the work-island stage (never portaled). The panel is
          the SAME element as the classic column (detailPanel), cloned with the
          island flag + an in-island collapse handler. */}
      {islandDesktop && infoIslandEl && createPortal(
        isAudio ? (
          /* Audio: the info island hosts the Playlist (COMING SOON) — NOT
             VideoDetailPanel (the cover-side AudioOverviewSide + stage-head
             AudioToolsMenu already carry the audio Overview, so portaling the
             panel here would duplicate it). */
          <PlaylistIsland title={titleText} author={video.author || undefined} coverUrl={audioCoverUrl} />
        ) : (
          React.cloneElement(detailPanel, { island: true, onCollapse: () => setInfoVisible(false) })
        ),
        infoIslandEl,
      )}

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
