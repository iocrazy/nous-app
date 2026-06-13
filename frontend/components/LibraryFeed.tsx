import React, { useRef, useEffect, useState, useCallback } from 'react';
import Hls from 'hls.js';
import { Video } from '../types';
import {
  Heart, MessageCircle, Share2, Music, User, Plus, Play, Pause, Volume2, VolumeX, Image as ImageIcon, Check, ChevronDown, ChevronUp, Loader2
} from 'lucide-react';
import { isVideoType, getVideoUrl, getCoverUrl } from '../utils/awemeType';
import { useAuth } from '../contexts/AuthContext';

interface LibraryFeedProps {
  data: Video[];
  /** Whether there are more pages to fetch. Disables sentinel load when false. */
  hasMore?: boolean;
  /** True while a page fetch is in flight — drives the spinner at the end. */
  isLoadingMore?: boolean;
  /** Called when the feed's internal sentinel enters the scroll container
   *  (with a preload margin). The feed owns its own observer so the root is
   *  the feed's scroll container, not the viewport. */
  onLoadMore?: () => void;
}

export const LibraryFeed: React.FC<LibraryFeedProps> = ({
  data,
  hasMore,
  isLoadingMore,
  onLoadMore,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [isMuted, setIsMuted] = useState(true);

  // Intersection Observer to handle auto-play on scroll
  useEffect(() => {
    const options = {
      root: containerRef.current,
      threshold: 0.6, // Trigger when 60% of the video is visible
    };

    const handleIntersection = (entries: IntersectionObserverEntry[]) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          const id = entry.target.getAttribute('data-id');
          if (id) setPlayingId(id);
        }
      });
    };

    const observer = new IntersectionObserver(handleIntersection, options);

    const elements = containerRef.current?.querySelectorAll('.feed-item');
    elements?.forEach((el) => observer.observe(el));

    return () => observer.disconnect();
  }, [data]);

  // Infinite scroll: load more pages when the sentinel enters the feed's
  // scroll container. Critically, root = containerRef so the observer fires
  // when the user swipes near the end of the feed (not when the whole feed
  // component first mounts into the viewport).
  useEffect(() => {
    if (!onLoadMore || !hasMore || !sentinelRef.current || !containerRef.current) {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const [entry] = entries;
        if (entry.isIntersecting && hasMore && !isLoadingMore) {
          onLoadMore();
        }
      },
      {
        root: containerRef.current,
        threshold: 0.01,
        // Preload a couple screens ahead so the next videos are fetched
        // BEFORE the user hits the true end — avoids the "stuck" feeling.
        rootMargin: '800px',
      },
    );
    observer.observe(sentinelRef.current);
    return () => observer.disconnect();
  }, [data.length, hasMore, isLoadingMore, onLoadMore]);

  const togglePlay = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (playingId === id) {
      setPlayingId(null); // Pause
    } else {
      setPlayingId(id); // Play
    }
  };

  const toggleMute = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsMuted(!isMuted);
  };

  const formatNumber = (num?: number) => {
    if (!num) return '0';
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
    return num.toString();
  };

  return (
    // Fixed viewport height: 100dvh minus TopBar(56px) + tab bar(80px)
    // Uses dvh (dynamic viewport height) for mobile browser chrome handling
    <div className="flex justify-center w-full bg-black" style={{ height: 'calc(100dvh - 136px)' }}>
      <div 
        ref={containerRef}
        className="w-full max-w-[500px] h-full bg-black md:rounded-2xl overflow-y-scroll snap-y snap-mandatory relative scrollbar-hide md:border border-ink-800 shadow-2xl"
        style={{ scrollBehavior: 'smooth' }}
      >
        {data.map((item, idx) => {
          const playingIdx = data.findIndex(d => d.platform_id === playingId);
          const shouldLoadVideo = Math.abs(idx - playingIdx) <= 1;
          return (
            <FeedItem
              key={item.platform_id}
              item={item}
              isPlaying={playingId === item.platform_id}
              shouldLoadVideo={shouldLoadVideo}
              isMuted={isMuted}
              onTogglePlay={(e) => togglePlay(e, item.platform_id)}
              onToggleMute={toggleMute}
              formatNumber={formatNumber}
            />
          );
        })}
        {data.length === 0 && (
           <div className="h-full flex flex-col items-center justify-center text-ink-500 gap-4">
             <p>No videos found in this feed.</p>
           </div>
        )}

        {/* Infinite-scroll sentinel + loading spinner. Sits AFTER the last
            feed item so the observer (root = this container) fires when the
            user scrolls near the end. rootMargin=800px means the next page
            starts fetching ~2 videos before the true end. */}
        {data.length > 0 && hasMore && (
          <div
            ref={sentinelRef}
            className="w-full flex items-center justify-center py-6 text-ink-400"
            aria-hidden={!isLoadingMore}
          >
            {isLoadingMore ? (
              <Loader2 size={20} className="animate-spin" />
            ) : (
              <span className="text-xs text-ink-600">Loading more…</span>
            )}
          </div>
        )}
        {data.length > 0 && !hasMore && (
          <div className="w-full py-6 flex justify-center">
            <span className="text-xs text-ink-700">
              All {data.length} videos loaded
            </span>
          </div>
        )}
      </div>
    </div>
  );
};

// Sub-component for individual slides
const FeedItem = ({
  item,
  isPlaying,
  shouldLoadVideo,
  isMuted,
  onTogglePlay,
  onToggleMute,
  formatNumber
}: {
  item: Video;
  isPlaying: boolean;
  shouldLoadVideo: boolean;
  isMuted: boolean;
  onTogglePlay: (e: React.MouseEvent) => void;
  onToggleMute: (e: React.MouseEvent) => void;
  formatNumber: (n?: number) => string;
}) => {
  const { mediaToken } = useAuth();
  const videoRef = useRef<HTMLVideoElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const descRef = useRef<HTMLParagraphElement>(null);
  const [copiedShare, setCopiedShare] = useState(false);
  const [descExpanded, setDescExpanded] = useState(false);
  const [descClamped, setDescClamped] = useState(false);
  const isVideo = isVideoType(item.media_type);

  // Detect if description text is clamped (overflows 2 lines)
  useEffect(() => {
    const el = descRef.current;
    if (el) {
      setDescClamped(el.scrollHeight > el.clientHeight + 1);
    }
  }, [item.description]);

  const toggleDesc = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setDescExpanded(prev => !prev);
  }, []);
  const [isBuffering, setIsBuffering] = useState(true);

  // 视频 URL: 优先使用 download_path
  const videoUrl = getVideoUrl(item, mediaToken ?? undefined);
  // 封面 URL
  const coverUrl = getCoverUrl(item, mediaToken ?? undefined);
  const isHlsUrl = videoUrl?.endsWith('.m3u8') ?? false;

  const imageUrl = item.image_download_urls?.[0] || coverUrl || null;

  // Setup HLS.js for .m3u8 video playback with tight buffer limits
  useEffect(() => {
    if (!isHlsUrl || !videoRef.current || !videoUrl) return;

    if (Hls.isSupported()) {
      const hls = new Hls({
        maxBufferLength: 10,
        maxBufferSize: 10 * 1024 * 1024,
        maxMaxBufferLength: 20,
      });
      hls.loadSource(videoUrl);
      hls.attachMedia(videoRef.current);
      hlsRef.current = hls;
    } else if (videoRef.current.canPlayType('application/vnd.apple.mpegurl')) {
      videoRef.current.src = videoUrl;
    }

    // Explicit release on unmount: free decoder buffers
    const videoEl = videoRef.current;
    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
      if (videoEl) {
        videoEl.pause();
        videoEl.removeAttribute('src');
        videoEl.load();
      }
    };
  }, [videoUrl, isHlsUrl]);

  useEffect(() => {
    if (videoRef.current) {
      if (isPlaying) {
        const playPromise = videoRef.current.play();
        if (playPromise !== undefined) {
          playPromise.catch(() => {
            // Auto-play was prevented
          });
        }
      } else {
        videoRef.current.pause();
      }
    }
  }, [isPlaying]);

  // Pause when tab/app hidden to save bandwidth + decoder
  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.hidden && videoRef.current) {
        videoRef.current.pause();
      }
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => document.removeEventListener('visibilitychange', onVisibilityChange);
  }, []);

  return (
    <div 
      data-id={item.platform_id}
      className="feed-item w-full h-full snap-center relative bg-black overflow-hidden shrink-0 group flex items-center justify-center"
      onClick={onTogglePlay}
    >
      {/* Media Layer — only mount <video> for current ± 1 items */}
      {isVideo && shouldLoadVideo ? (
        <>
          {/* Buffering spinner */}
          {isBuffering && (
            <div className="absolute inset-0 flex items-center justify-center z-10 bg-black">
              {coverUrl ? (
                <img src={coverUrl} alt="" className="absolute inset-0 w-full h-full object-contain opacity-30" />
              ) : null}
              <Loader2 className="w-10 h-10 text-white/60 animate-spin" />
            </div>
          )}
          <video
            ref={videoRef}
            src={isHlsUrl ? undefined : videoUrl}
            className="w-full h-full object-contain cursor-pointer bg-black"
            loop
            muted={isMuted}
            playsInline
            preload={isPlaying ? 'auto' : 'metadata'}
            onCanPlay={() => setIsBuffering(false)}
            onWaiting={() => setIsBuffering(true)}
            onPlaying={() => setIsBuffering(false)}
          />
        </>
      ) : isVideo ? (
        // Not loaded yet — show cover placeholder
        <div className="w-full h-full flex items-center justify-center bg-black">
          {coverUrl ? (
            <img src={coverUrl} alt="" className="w-full h-full object-contain" loading="lazy" />
          ) : (
            <Loader2 className="w-8 h-8 text-ink-600 animate-spin" />
          )}
        </div>
      ) : (
        <div className="w-full h-full relative flex items-center justify-center bg-black">
            {imageUrl ? (
              <img src={imageUrl} alt={item.title} className="w-full h-full object-contain" loading="lazy" />
            ) : (
              <div className="flex items-center justify-center">
                <ImageIcon size={48} className="text-ink-700" />
              </div>
            )}
            <div className="absolute top-4 right-4 bg-black/50 px-3 py-1 rounded-full text-xs flex items-center gap-1 backdrop-blur-md">
                <ImageIcon size={12} />
                Image Mode
            </div>
        </div>
      )}

      {/* Controls Overlay (Play/Pause/Mute) */}
      <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
        {!isPlaying && isVideo && (
          <div className="w-16 h-16 bg-black/40 rounded-full flex items-center justify-center backdrop-blur-sm animate-in fade-in zoom-in duration-200">
            <Play className="fill-white text-white ml-1 w-8 h-8" />
          </div>
        )}
      </div>

      {/* Right Sidebar Actions — Douyin style */}
      <div className="absolute bottom-20 sm:bottom-20 right-2 flex flex-col items-center gap-4 z-20">
        <div className="relative">
          <div className="w-10 h-10 rounded-full border border-white bg-ink-800 overflow-hidden">
             <div className="w-full h-full flex items-center justify-center text-ink-500">
                <User size={20} />
             </div>
          </div>
          <div className="absolute -bottom-2 left-1/2 -translate-x-1/2 w-5 h-5 bg-rose-500 rounded-full flex items-center justify-center text-white border-2 border-black">
             <Plus size={12} />
          </div>
        </div>

        <div className="flex flex-col items-center gap-1">
           <button className="p-2 rounded-full bg-black/20 hover:bg-black/40 backdrop-blur-sm transition-colors group-hover/btn:scale-110">
              <Heart className="w-8 h-8 text-white drop-shadow-md" />
           </button>
           <span className="text-white text-xs font-semibold drop-shadow-md">{formatNumber(item.like_count)}</span>
        </div>

        <div className="flex flex-col items-center gap-1">
           <button className="p-2 rounded-full bg-black/20 hover:bg-black/40 backdrop-blur-sm transition-colors">
              <MessageCircle className="w-8 h-8 text-white drop-shadow-md" />
           </button>
           <span className="text-white text-xs font-semibold drop-shadow-md">{formatNumber(item.comment_count)}</span>
        </div>

        <div className="flex flex-col items-center gap-1">
           <button
             className="p-2 rounded-full bg-black/20 hover:bg-black/40 backdrop-blur-sm transition-colors"
             onClick={(e) => {
               e.stopPropagation();
               if (item.original_url) {
                 navigator.clipboard.writeText(item.original_url);
                 setCopiedShare(true);
                 setTimeout(() => setCopiedShare(false), 2000);
               }
             }}
             title="Click to copy link"
           >
              {copiedShare ? (
                <Check className="w-8 h-8 text-emerald-400 drop-shadow-md" />
              ) : (
                <Share2 className="w-8 h-8 text-white drop-shadow-md" />
              )}
           </button>
           <span className="text-white text-xs font-semibold drop-shadow-md">{copiedShare ? 'Copied!' : formatNumber(item.share_count)}</span>
        </div>

        {/* Mute — bottom of sidebar, Douyin style */}
        {isVideo && (
          <button
            onClick={onToggleMute}
            className="p-2 rounded-full bg-black/20 hover:bg-black/40 backdrop-blur-sm transition-colors"
          >
            {isMuted ? <VolumeX className="w-7 h-7 text-white drop-shadow-md" /> : <Volume2 className="w-7 h-7 text-white drop-shadow-md" />}
          </button>
        )}
      </div>

      {/* Bottom Content Info — pb-16 on mobile to clear tab bar */}
      <div className="absolute bottom-0 left-0 right-0 p-4 pb-4 bg-gradient-to-t from-black/90 via-black/40 to-transparent pt-20 z-10">
        <div className="max-w-[80%]">
          <h3 className="text-white font-bold text-base mb-1 drop-shadow-md cursor-pointer hover:underline">@{item.author || 'User'}</h3>
          <div className="relative">
            <p
              ref={descRef}
              className={`text-ink-100 text-sm drop-shadow-md leading-relaxed ${descExpanded ? '' : 'line-clamp-1'}`}
            >
              {item.description}
            </p>
            {(descClamped || descExpanded) && (
              <button
                onClick={toggleDesc}
                className="text-ink-300 text-xs font-medium mt-0.5 flex items-center gap-0.5 hover:text-white transition-colors"
              >
                {descExpanded ? (
                  <><ChevronUp size={12} /> less</>
                ) : (
                  <><ChevronDown size={12} /> more</>
                )}
              </button>
            )}
          </div>
          <div className="flex items-center gap-2 text-white/80 mt-1.5">
             <Music size={14} />
             <div className="text-xs overflow-hidden w-40">
                <div className="whitespace-nowrap animate-marquee">
                   {item.music_name || 'Original Audio'}
                </div>
             </div>
          </div>
        </div>
      </div>
    </div>
  );
};
