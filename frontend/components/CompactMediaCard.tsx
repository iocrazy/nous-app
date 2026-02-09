
import React, { useState } from 'react';
import { Video } from '../types';
import { Video as VideoIcon, Image as ImageIcon, Heart, Play, MessageCircle, Share2, Bookmark, User, ChevronLeft, ChevronRight, Users, Check, FileText, Sparkles, Eye } from 'lucide-react';
import { isVideoType, getVideoUrl, getCoverUrl } from '../utils/awemeType';

const getPlatformLabel = (platform?: string): string => {
  if (!platform) return '';
  const labels: Record<string, string> = {
    bilibili: 'B\u7AD9',
    youtube: 'YouTube',
    twitter: 'X',
    tiktok: 'TikTok',
    xiaohongshu: '\u5C0F\u7EA2\u4E66',
  };
  return labels[platform] || platform.charAt(0).toUpperCase() + platform.slice(1);
};

interface CompactMediaCardProps {
  data: Video;
  onClick: () => void;
  isShared?: boolean;
}

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

// Helper for consistent tag colors matching the neon dark aesthetic
const getTagColor = (tag: string) => {
  const colors = [
    'text-pink-300 bg-pink-500/10 border-pink-500/20', 
    'text-blue-300 bg-blue-500/10 border-blue-500/20',
    'text-emerald-300 bg-emerald-500/10 border-emerald-500/20',
    'text-amber-300 bg-amber-500/10 border-amber-500/20',
    'text-violet-300 bg-violet-500/10 border-violet-500/20',
    'text-cyan-300 bg-cyan-500/10 border-cyan-500/20',
    'text-rose-300 bg-rose-500/10 border-rose-500/20',
  ];
  let hash = 0;
  for (let i = 0; i < tag.length; i++) hash = tag.charCodeAt(i) + ((hash << 5) - hash);
  return colors[Math.abs(hash) % colors.length];
};

export const CompactMediaCard: React.FC<CompactMediaCardProps> = ({ data, onClick, isShared }) => {
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentImageIndex, setCurrentImageIndex] = useState(0);
  const [copiedShare, setCopiedShare] = useState(false);
  const [imageError, setImageError] = useState(false);
  
  const isVideo = isVideoType(data.media_type);
  const images = data.image_download_urls || [];
  const isAlbum = !isVideo && images.length > 1;

  // 封面 URL: 如果是图集则使用当前索引，否则使用封面
  const coverUrl = isAlbum && images.length > 0
    ? images[currentImageIndex]
    : (getCoverUrl(data) || "https://picsum.photos/400/600");

  // 视频 URL: 优先使用 download_path
  const videoUrl = getVideoUrl(data);

  const formatNumber = (num?: number) => {
    if (!num) return '0';
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
    return num.toString();
  };

  const formatDate = (isoString?: string) => {
    if (!isoString) return '';
    try {
      const date = new Date(isoString);
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, '0');
      const day = String(date.getDate()).padStart(2, '0');
      return `${year}-${month}-${day}`;
    } catch { return ''; }
  };

  const handleSlide = (e: React.MouseEvent, direction: 'left' | 'right') => {
    e.stopPropagation();
    setImageError(false); // Reset error state when switching images
    if (direction === 'left') {
        setCurrentImageIndex(prev => (prev === 0 ? images.length - 1 : prev - 1));
    } else {
        setCurrentImageIndex(prev => (prev === images.length - 1 ? 0 : prev + 1));
    }
  };

  return (
    <div
      className="group relative flex flex-col bg-zinc-900 rounded-xl overflow-hidden border border-zinc-800 hover:border-zinc-600 transition-all active:scale-[0.98] shadow-sm"
    >
      {/* Thumbnail Container - Handles Hover Playback & Sliding */}
      <div 
        className="relative w-full overflow-hidden bg-black aspect-[3/4] cursor-pointer"
        onClick={onClick}
        onMouseEnter={() => isVideo && setIsPlaying(true)}
        onMouseLeave={() => isVideo && setIsPlaying(false)}
      >
        {isPlaying ? (
            <video
                src={videoUrl}
                className="w-full h-full object-contain bg-black"
                muted
                loop
                autoPlay
                playsInline
                controls
                onClick={(e) => e.stopPropagation()} // Prevent card navigation when interacting with video/controls
            />
        ) : (
            <>
                {imageError ? (
                  <div className="w-full h-full flex flex-col items-center justify-center bg-zinc-800">
                    <ImageIcon size={32} className="text-zinc-600 mb-2" />
                    <span className="text-xs text-zinc-500">Image unavailable</span>
                  </div>
                ) : (
                  <img
                    src={coverUrl}
                    alt={data.title}
                    className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105 opacity-90 block"
                    referrerPolicy="no-referrer"
                    onError={() => setImageError(true)}
                  />
                )}
                
                {/* Carousel Controls for Albums */}
                {isAlbum && (
                  <>
                    <button 
                      onClick={(e) => handleSlide(e, 'left')}
                      className="absolute left-2 top-1/2 -translate-y-1/2 p-1.5 bg-black/50 hover:bg-black/70 rounded-full text-white opacity-0 group-hover:opacity-100 transition-opacity z-20"
                    >
                      <ChevronLeft size={16} />
                    </button>
                    <button 
                      onClick={(e) => handleSlide(e, 'right')}
                      className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 bg-black/50 hover:bg-black/70 rounded-full text-white opacity-0 group-hover:opacity-100 transition-opacity z-20"
                    >
                      <ChevronRight size={16} />
                    </button>
                    {/* Index Indicator */}
                    <div className="absolute top-2 left-2 bg-black/60 backdrop-blur-sm px-2 py-0.5 rounded-full text-[10px] text-white/90 font-medium z-10 pointer-events-none">
                      {currentImageIndex + 1}/{images.length}
                    </div>
                  </>
                )}
                
                {/* Platform Badge */}
                {data.source_platform && data.source_platform !== 'douyin' && (
                  <div className={`absolute ${isAlbum ? 'top-9' : 'top-2'} left-2 z-10 pointer-events-none`}>
                    <span className="bg-black/70 backdrop-blur-sm px-2 py-0.5 rounded-full text-[10px] text-white/90 font-medium">
                      {getPlatformLabel(data.source_platform)}
                    </span>
                  </div>
                )}

                {/* Type Indicator (Right side) */}
                <div className="absolute top-2 right-2 flex items-center gap-1.5 z-10 pointer-events-none">
                  {isShared && (
                    <div className="bg-indigo-500/90 backdrop-blur-sm p-1.5 rounded-full text-white" title="Shared in Team">
                      <Users size={12} />
                    </div>
                  )}
                  <div className="bg-black/60 backdrop-blur-sm p-1.5 rounded-full text-white/90">
                    {isVideo ? <VideoIcon size={12} /> : <ImageIcon size={12} />}
                  </div>
                </div>

                {/* Play Overlay (Desktop Hover - Only for Video) */}
                {isVideo && (
                    <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-black/20 z-10 pointer-events-none">
                      <div className="bg-white/20 backdrop-blur-md p-2 rounded-full">
                          <Play size={20} className="text-white fill-white" />
                      </div>
                    </div>
                )}

                {/* Gradient Overlay */}
                <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent opacity-60 pointer-events-none" />
                
                {/* Title Overlay */}
                <div className="absolute bottom-0 left-0 right-0 p-3 z-10 pointer-events-none">
                  <h3 className="text-[12px] text-white font-medium line-clamp-2 leading-tight drop-shadow-md">
                    {data.title || 'Untitled Media'}
                  </h3>
                </div>
            </>
        )}
      </div>

      {/* Info & Actions Section - Handles Detail View */}
      <div 
        className="p-3 flex flex-col gap-3 bg-zinc-900 cursor-pointer"
        onClick={onClick}
      >
        
        {/* Tags Row - Single line only */}
        {data.tags && data.tags.length > 0 ? (
           <div className="flex items-center gap-1.5 overflow-hidden">
              {data.tags.slice(0, 3).map((tag, i) => (
                 <span key={i} className={`text-[10px] px-2 py-0.5 rounded border font-medium whitespace-nowrap ${getTagColor(tag)}`}>
                    #{tag}
                 </span>
              ))}
              {data.tags.length > 3 && (
                 <span className="text-[10px] px-2 py-0.5 rounded bg-zinc-800 text-zinc-500 border border-zinc-700 whitespace-nowrap">+{data.tags.length - 3}</span>
              )}
           </div>
        ) : (
           <div className="h-0" />
        )}

        {/* AI Status Icons */}
        <div className="flex items-center gap-2">
          <div title={`Transcript: ${data.transcript_status || 'pending'}`}>
            <FileText size={12} className={getAIStatusClass(data.transcript_status)} />
          </div>
          <div title={`Summary: ${data.summary_status || 'pending'}`}>
            <Sparkles size={12} className={getAIStatusClass(data.summary_status)} />
          </div>
          <div title={`Visual Analysis: ${data.visual_analysis_status || 'pending'}`}>
            <Eye size={12} className={getAIStatusClass(data.visual_analysis_status)} />
          </div>
        </div>

        {/* Stats Grid */}
        <div className="grid grid-cols-4 gap-2">
           {/* Like */}
           <div className="flex flex-col items-center justify-center py-2 bg-[#2A1818] rounded-lg border border-red-900/30">
              <Heart size={14} className="text-rose-500 mb-1" />
              <span className="text-[10px] text-white font-bold">{formatNumber(data.like_count)}</span>
           </div>
           {/* Comment */}
           <div className="flex flex-col items-center justify-center py-2 bg-[#10243E] rounded-lg border border-sky-900/30">
              <MessageCircle size={14} className="text-sky-500 mb-1" />
              <span className="text-[10px] text-white font-bold">{formatNumber(data.comment_count)}</span>
           </div>
           {/* Share - Click to copy link */}
           <button
              onClick={(e) => {
                e.stopPropagation();
                if (data.original_url) {
                  navigator.clipboard.writeText(data.original_url);
                  setCopiedShare(true);
                  setTimeout(() => setCopiedShare(false), 2000);
                }
              }}
              className="flex flex-col items-center justify-center py-2 bg-[#0F291E] rounded-lg border border-emerald-900/30 hover:border-emerald-500/50 hover:bg-emerald-500/10 transition-all cursor-pointer group"
              title="Click to copy link"
           >
              {copiedShare ? (
                <Check size={14} className="text-emerald-400 mb-1" />
              ) : (
                <Share2 size={14} className="text-emerald-500 mb-1 group-hover:scale-110 transition-transform" />
              )}
              <span className="text-[10px] text-white font-bold">{copiedShare ? 'Copied!' : formatNumber(data.share_count)}</span>
           </button>
           {/* Collect */}
           <div className="flex flex-col items-center justify-center py-2 bg-[#2E2005] rounded-lg border border-amber-900/30">
              <Bookmark size={14} className="text-amber-500 mb-1" />
              <span className="text-[10px] text-white font-bold">{formatNumber(data.favorite_count)}</span>
           </div>
        </div>

        {/* Author Footer with Date - Inline Layout */}
        <div className="flex items-center pt-2 border-t border-zinc-800/50 mt-1 gap-2">
             <div className="w-5 h-5 rounded-full bg-zinc-800 flex items-center justify-center overflow-hidden flex-shrink-0 border border-zinc-700">
                <User size={12} className="text-zinc-500" />
             </div>
             <div className="flex items-center min-w-0 flex-1">
                <span className="text-[11px] text-zinc-400 font-medium truncate">@{data.author || 'User'}</span>
                <span className="text-[11px] text-zinc-600 mx-1.5">·</span>
                <span className="text-[10px] text-zinc-500 font-mono flex-shrink-0">
                   {formatDate(data.published_at)}
                </span>
             </div>
        </div>
      </div>
    </div>
  );
};
