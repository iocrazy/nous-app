import React from 'react';
import {
  Loader2,
  Video as VideoIcon,
  Image as ImageIcon,
  Music,
  CloudDownload,
  Download,
} from 'lucide-react';
import { Video } from '../../types';
import { isVideoType, isAlbumType } from '../../utils/awemeType';

interface DownloadMenuDropdownProps {
  video: Video;
  isDownloading: boolean;
  isFetching: boolean;
  onClose: () => void;
  onDownload: (type: 'video' | 'cover' | 'audio' | 'images') => void;
  onFetchMedia: (options: { video?: boolean; cover?: boolean }) => void;
  onExtractAudio: () => void;
  /** Re-download a qishui (Soda) audio track via the soda path. */
  onFetchSodaAudio?: () => void;
}

export function DownloadMenuDropdown({
  video,
  isDownloading,
  isFetching,
  onClose,
  onDownload,
  onFetchMedia,
  onExtractAudio,
  onFetchSodaAudio,
}: DownloadMenuDropdownProps) {
  const isCompleted = (s?: string) => s?.toLowerCase() === 'completed';
  const isPending = (s?: string) => { const l = s?.toLowerCase(); return l === 'pending' || l === 'downloading'; };
  const isFailed = (s?: string) => s?.toLowerCase() === 'failed';

  // Defense: if status says "completed" but no actual file path, treat as unfetched.
  // Audio can come from two sources: extract_audio_path (ffmpeg-extracted from the
  // video) OR music_download_path (separate BGM download). Either counts.
  const hasVideoFile = !!(video.download_path || video.hls_path);
  const hasCoverFile = !!video.cover_download_path;
  const hasAudioFile = !!(video.extract_audio_path || video.music_download_path);
  // qishui covers are best-effort at download time and shown in the player
  // hero. The Retry/Fetch-Cover actions call the douyin/yt-dlp generic
  // re-fetch endpoint, which can't service a qishui cover — so for qishui we
  // only expose "Cover" when it already succeeded, never a (broken) retry.
  const isQishui = video.source_platform === 'qishui';

  const videoStatus = isCompleted(video.video_download_status) && !hasVideoFile ? undefined : video.video_download_status;
  const coverStatus = isCompleted(video.cover_download_status) && !hasCoverFile ? undefined : video.cover_download_status;
  const audioStatus = isCompleted(video.music_download_status) && !hasAudioFile ? undefined : video.music_download_status;

  const btnClass = "w-full px-3 py-1.5 text-left text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2 transition-colors";
  const disabledClass = "w-full px-3 py-1.5 text-left text-xs text-zinc-500 flex items-center gap-2 cursor-default";

  return (
    <>
      <div className="fixed inset-0 z-10" onClick={onClose} />
      <div className="absolute right-0 top-full mt-1 z-20 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl overflow-hidden min-w-[180px]">
        <div className="py-1">
          {/* Video */}
          {isVideoType(video.media_type) && (
            isCompleted(videoStatus) ? (
              <button onClick={() => onDownload('video')} className={btnClass}>
                <VideoIcon size={13} className="text-indigo-400" /> Video
              </button>
            ) : isPending(videoStatus) ? (
              <button disabled className={disabledClass}>
                <Loader2 size={13} className="text-indigo-400 animate-spin" /> Video Downloading...
              </button>
            ) : isFailed(videoStatus) ? (
              <button onClick={() => onFetchMedia({ video: true })} className={btnClass}>
                <CloudDownload size={13} className="text-red-400" /> Retry Video
              </button>
            ) : video.original_url ? (
              <button onClick={() => onFetchMedia({ video: true })} className={btnClass}>
                <CloudDownload size={13} className="text-indigo-400" /> Fetch Video
              </button>
            ) : null
          )}
          {/* Gallery (image-text / carousel / 动图) — packaged zip.
              Show for any album-type post; the backend zip endpoint
              serves whatever is on disk (images + videos together). */}
          {isAlbumType(video.media_type) && (
            <button onClick={() => onDownload('images')} className={btnClass}>
              <ImageIcon size={13} className="text-pink-400" /> Gallery
              {video.image_download_urls && video.image_download_urls.length > 0
                ? ` (${video.image_download_urls.length})`
                : ''}
            </button>
          )}
          {/* Cover */}
          {isCompleted(coverStatus) ? (
            <button onClick={() => onDownload('cover')} className={btnClass}>
              <ImageIcon size={13} className="text-emerald-400" /> Cover
            </button>
          ) : isQishui ? (
            // qishui covers can't be fetched via the douyin/yt-dlp generic path;
            // re-run the soda download, which tops up a missing cover (#474).
            onFetchSodaAudio ? (
              <button onClick={onFetchSodaAudio} className={btnClass}>
                <CloudDownload size={13} className={isFailed(coverStatus) ? 'text-red-400' : 'text-emerald-400'} />{' '}
                {isFailed(coverStatus) ? 'Retry Cover' : 'Fetch Cover'}
              </button>
            ) : null
          ) : isPending(coverStatus) ? (
            <button disabled className={disabledClass}>
              <Loader2 size={13} className="text-emerald-400 animate-spin" /> Cover Downloading...
            </button>
          ) : isFailed(coverStatus) ? (
            <button onClick={() => onFetchMedia({ cover: true })} className={btnClass}>
              <CloudDownload size={13} className="text-red-400" /> Retry Cover
            </button>
          ) : video.original_url ? (
            <button onClick={() => onFetchMedia({ cover: true })} className={btnClass}>
              <CloudDownload size={13} className="text-emerald-400" /> Fetch Cover
            </button>
          ) : null}
          {/* Audio */}
          {(() => {
            const isAlbum = isAlbumType(video.media_type);
            if (hasAudioFile) {
              return (
                <button onClick={() => onDownload('audio')} className={btnClass}>
                  <Music size={13} className="text-amber-400" /> Audio
                </button>
              );
            }
            if (isPending(audioStatus)) {
              return (
                <button disabled className={disabledClass}>
                  <Loader2 size={13} className="text-amber-400 animate-spin" /> {isAlbum ? 'Audio Downloading...' : 'Audio Extracting...'}
                </button>
              );
            }
            if (isAlbum && video.original_url) {
              return (
                <button onClick={() => onFetchMedia({ video: false, cover: false })} className={btnClass}>
                  <Music size={13} className="text-amber-400" /> Fetch Audio
                </button>
              );
            }
            // qishui (Soda) audio-only track: re-download via the soda path
            // (the douyin/yt-dlp fetch can't service it). Without this, a
            // missing-audio soda track had NO actionable menu item.
            if (isQishui && onFetchSodaAudio) {
              return (
                <button onClick={onFetchSodaAudio} className={btnClass}>
                  <CloudDownload size={13} className="text-amber-400" />{' '}
                  {isFailed(audioStatus) ? 'Retry Audio' : 'Fetch Audio'}
                </button>
              );
            }
            if (!isAlbum && hasVideoFile) {
              return (
                <button onClick={onExtractAudio} className={btnClass}>
                  <Music size={13} className="text-amber-400" /> Extract Audio
                </button>
              );
            }
            return null;
          })()}
        </div>
      </div>
    </>
  );
}

interface MobileDownloadMenuProps {
  video: Video;
  onClose: () => void;
  onDownload: (type: 'video' | 'cover' | 'audio' | 'images') => void;
  onFetchMedia: (options: { video?: boolean; cover?: boolean }) => void;
  onFetchSodaAudio?: () => void;
}

export function MobileDownloadMenu({
  video,
  onClose,
  onDownload,
  onFetchMedia,
  onFetchSodaAudio,
}: MobileDownloadMenuProps) {
  const isCompleted = (s?: string) => s?.toLowerCase() === 'completed';
  const hasVideoFile = !!(video.download_path || video.hls_path);
  const hasCoverFile = !!video.cover_download_path;
  const hasAudioFile = !!(video.extract_audio_path || video.music_download_path);
  const btnClass = "flex items-center gap-2 w-full px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors";

  return (
    <>
      {/* Video: only for non-album (真正的视频) */}
      {!isAlbumType(video.media_type) && isCompleted(video.video_download_status) && hasVideoFile && (
        <button onClick={() => { onClose(); onDownload('video'); }} className={btnClass}>
          <Download size={13} className="text-indigo-400" /> Download Video
        </button>
      )}
      {!isAlbumType(video.media_type) && !isCompleted(video.video_download_status) && video.original_url && (
        <button onClick={() => { onClose(); onFetchMedia({ video: true }); }} className={btnClass}>
          <CloudDownload size={13} className="text-indigo-400" /> Fetch Video
        </button>
      )}
      {/* Gallery (image-text / carousel / 动图): packaged zip */}
      {isAlbumType(video.media_type) && (
        <button onClick={() => { onClose(); onDownload('images'); }} className={btnClass}>
          <Download size={13} className="text-pink-400" /> Download Gallery
        </button>
      )}
      {isCompleted(video.cover_download_status) && hasCoverFile && (
        <button onClick={() => { onClose(); onDownload('cover'); }} className={btnClass}>
          <Download size={13} className="text-emerald-400" /> Download Cover
        </button>
      )}
      {hasAudioFile && (
        <button onClick={() => { onClose(); onDownload('audio'); }} className={btnClass}>
          <Download size={13} className="text-amber-400" /> Download Audio
        </button>
      )}
      {/* qishui (Soda) audio with no file → re-download via the soda path */}
      {!hasAudioFile && video.source_platform === 'qishui' && onFetchSodaAudio && (
        <button onClick={() => { onClose(); onFetchSodaAudio(); }} className={btnClass}>
          <CloudDownload size={13} className="text-amber-400" /> Fetch Audio
        </button>
      )}
    </>
  );
}
