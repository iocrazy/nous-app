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
}

export function DownloadMenuDropdown({
  video,
  isDownloading,
  isFetching,
  onClose,
  onDownload,
  onFetchMedia,
  onExtractAudio,
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
          {/* Images (carousel/image content) */}
          {video.image_download_urls && video.image_download_urls.length > 0 && (
            <button onClick={() => onDownload('images')} className={btnClass}>
              <ImageIcon size={13} className="text-pink-400" /> Images ({video.image_download_urls.length})
            </button>
          )}
          {/* Cover */}
          {isCompleted(coverStatus) ? (
            <button onClick={() => onDownload('cover')} className={btnClass}>
              <ImageIcon size={13} className="text-emerald-400" /> Cover
            </button>
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
                  <Music size={13} className="text-amber-400" /> Download Audio
                </button>
              );
            }
            if (isPending(audioStatus)) {
              return (
                <button disabled className={disabledClass}>
                  <Loader2 size={13} className="text-amber-400 animate-spin" /> {isAlbum ? 'Downloading Audio...' : 'Extracting Audio...'}
                </button>
              );
            }
            if (isAlbum && video.original_url) {
              return (
                <button onClick={() => onFetchMedia({ video: false, cover: false })} className={btnClass}>
                  <Music size={13} className="text-amber-400" /> Download Audio
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
}

export function MobileDownloadMenu({
  video,
  onClose,
  onDownload,
  onFetchMedia,
}: MobileDownloadMenuProps) {
  const isCompleted = (s?: string) => s?.toLowerCase() === 'completed';
  const hasVideoFile = !!(video.download_path || video.hls_path);
  const hasCoverFile = !!video.cover_download_path;
  const hasAudioFile = !!(video.extract_audio_path || video.music_download_path);
  const btnClass = "flex items-center gap-2 w-full px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors";

  return (
    <>
      {isCompleted(video.video_download_status) && hasVideoFile && (
        <button onClick={() => { onClose(); onDownload('video'); }} className={btnClass}>
          <Download size={13} className="text-indigo-400" /> {isAlbumType(video.media_type) ? 'Download Images' : 'Download Video'}
        </button>
      )}
      {!isCompleted(video.video_download_status) && video.original_url && (
        <button onClick={() => { onClose(); onFetchMedia({ video: true }); }} className={btnClass}>
          <CloudDownload size={13} className="text-indigo-400" /> {isAlbumType(video.media_type) ? 'Fetch Images' : 'Fetch Video'}
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
    </>
  );
}
