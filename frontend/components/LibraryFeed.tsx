import React, { useRef, useEffect, useState } from 'react';
import { DouyinBase } from '../types';
import {
  Heart, MessageCircle, Share2, Music, User, Plus, Play, Pause, Volume2, VolumeX, Image as ImageIcon
} from 'lucide-react';
import { isVideoType, getVideoUrl, getCoverUrl } from '../utils/awemeType';

interface LibraryFeedProps {
  data: DouyinBase[];
}

export const LibraryFeed: React.FC<LibraryFeedProps> = ({ data }) => {
  const containerRef = useRef<HTMLDivElement>(null);
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
    // Height adjusted: On mobile it takes full screen height minus bottom nav
    <div className="flex justify-center w-full h-[calc(100vh-65px)] md:h-[calc(100vh-180px)] bg-black">
      <div 
        ref={containerRef}
        className="w-full max-w-[500px] h-full bg-black md:rounded-2xl overflow-y-scroll snap-y snap-mandatory relative scrollbar-hide md:border border-zinc-800 shadow-2xl"
        style={{ scrollBehavior: 'smooth' }}
      >
        {data.map((item) => (
          <FeedItem 
            key={item.aweme_id} 
            item={item} 
            isPlaying={playingId === item.aweme_id}
            isMuted={isMuted}
            onTogglePlay={(e) => togglePlay(e, item.aweme_id)}
            onToggleMute={toggleMute}
            formatNumber={formatNumber}
          />
        ))}
        {data.length === 0 && (
           <div className="h-full flex flex-col items-center justify-center text-zinc-500 gap-4">
             <p>No videos found in this feed.</p>
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
  isMuted, 
  onTogglePlay, 
  onToggleMute,
  formatNumber 
}: { 
  item: DouyinBase; 
  isPlaying: boolean; 
  isMuted: boolean; 
  onTogglePlay: (e: React.MouseEvent) => void;
  onToggleMute: (e: React.MouseEvent) => void;
  formatNumber: (n?: number) => string;
}) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const isVideo = isVideoType(item.aweme_type);
  // 视频 URL: 优先使用 download_path
  const videoUrl = getVideoUrl(item);
  // 封面 URL
  const coverUrl = getCoverUrl(item);

  const imageUrl = item.image_download_urls?.[0] || "https://picsum.photos/400/800";

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

  return (
    <div 
      data-id={item.aweme_id}
      className="feed-item w-full h-full snap-center relative bg-black overflow-hidden shrink-0 group flex items-center justify-center"
      onClick={onTogglePlay}
    >
      {/* Media Layer */}
      {isVideo ? (
        <video
          ref={videoRef}
          src={videoUrl}
          // UPDATED: object-contain to ensure full video visibility
          className="w-full h-full object-contain cursor-pointer bg-black"
          loop
          muted={isMuted}
          playsInline
          poster={imageUrl} 
        />
      ) : (
        <div className="w-full h-full relative flex items-center justify-center bg-black">
            <img src={imageUrl} alt={item.video_title} className="w-full h-full object-contain" />
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

      {/* Mute Button */}
      {isVideo && (
        <button 
            onClick={onToggleMute}
            className="absolute top-12 right-4 p-2 bg-black/20 hover:bg-black/40 rounded-full backdrop-blur-md text-white transition-all z-20 mt-4 md:mt-0"
        >
            {isMuted ? <VolumeX size={20} /> : <Volume2 size={20} />}
        </button>
      )}

      {/* Right Sidebar Actions */}
      <div className="absolute bottom-20 right-2 flex flex-col items-center gap-4 z-20">
        <div className="relative">
          <div className="w-10 h-10 rounded-full border border-white bg-zinc-800 overflow-hidden">
             <div className="w-full h-full flex items-center justify-center text-zinc-500">
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
           <span className="text-white text-xs font-semibold drop-shadow-md">{formatNumber(item.video_digg_count)}</span>
        </div>

        <div className="flex flex-col items-center gap-1">
           <button className="p-2 rounded-full bg-black/20 hover:bg-black/40 backdrop-blur-sm transition-colors">
              <MessageCircle className="w-8 h-8 text-white drop-shadow-md" />
           </button>
           <span className="text-white text-xs font-semibold drop-shadow-md">{formatNumber(item.video_comment_count)}</span>
        </div>

        <div className="flex flex-col items-center gap-1">
           <button className="p-2 rounded-full bg-black/20 hover:bg-black/40 backdrop-blur-sm transition-colors">
              <Share2 className="w-8 h-8 text-white drop-shadow-md" />
           </button>
           <span className="text-white text-xs font-semibold drop-shadow-md">{formatNumber(item.video_share_count)}</span>
        </div>
      </div>

      {/* Bottom Content Info */}
      <div className="absolute bottom-0 left-0 right-0 p-4 bg-gradient-to-t from-black/90 via-black/40 to-transparent pt-20 z-10">
        <div className="max-w-[80%]">
          <h3 className="text-white font-bold text-lg mb-1 drop-shadow-md cursor-pointer hover:underline">@{item.author || 'User'}</h3>
          <p className="text-zinc-100 text-sm mb-2 line-clamp-2 drop-shadow-md leading-relaxed">
             {item.video_desc}
          </p>
          <div className="flex items-center gap-2 text-white/80 animate-pulse-slow">
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
