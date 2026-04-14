
import React, { useState, useRef, useCallback, useMemo } from 'react';
import { Video } from '../types';
import { Video as VideoIcon, Image as ImageIcon, Heart, Play, MessageCircle, Share2, Bookmark, User, ChevronLeft, ChevronRight, Users, Check, FileText, Sparkles, Eye } from 'lucide-react';
import { isVideoType, getCoverUrl, getVideoUrl } from '../utils/awemeType';
import { getPreviewSpriteUrl } from '../services/resourceService';
import { useAuth } from '../contexts/AuthContext';

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
  onClick: (e?: React.MouseEvent) => void;
  onDoubleClick?: () => void;
  onContextMenu?: (e: React.MouseEvent, data: Video) => void;
  isShared?: boolean;
  isSelected?: boolean;
  selectable?: boolean;
  isChecked?: boolean;
  onToggleSelect?: (e: React.MouseEvent) => void;
  forceShowCheckbox?: boolean;
  resourceId?: string;
  aiStatus?: { transcript_status?: string; summary_status?: string; visual_analysis_status?: string };
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

export const CompactMediaCard: React.FC<CompactMediaCardProps> = ({ data, onClick, onDoubleClick, onContextMenu, isShared, isSelected, selectable, isChecked, onToggleSelect, forceShowCheckbox, resourceId, aiStatus }) => {
  const { mediaToken } = useAuth();
  const [currentImageIndex, setCurrentImageIndex] = useState(0);
  const [copiedShare, setCopiedShare] = useState(false);
  const [imageError, setImageError] = useState(false);

  const isVideo = isVideoType(data.media_type);
  const images = data.image_download_urls || [];
  const isAlbum = !isVideo && images.length > 1;

  // 封面 URL: 如果是图集则使用当前索引，否则使用封面
  const coverUrl = isAlbum && images.length > 0
    ? images[currentImageIndex]
    : (getCoverUrl(data, mediaToken ?? undefined) || "https://picsum.photos/400/600");

  // --- Video seek scrub (universal fallback for any video) ---
  const videoUrl = isVideo ? getVideoUrl(data, mediaToken ?? undefined) : undefined;
  const [isVideoScrubbing, setIsVideoScrubbing] = useState(false);
  const videoScrubRef = useRef<HTMLVideoElement>(null);

  // --- Sprite hover scrub (when resourceId available) ---
  const hasSpriteSupport = isVideo && !!resourceId;
  const [isHovering, setIsHovering] = useState(false);
  const [spriteLoaded, setSpriteLoaded] = useState(false);
  const [spriteError, setSpriteError] = useState(false);
  const [scrubPercent, setScrubPercent] = useState(0);
  const thumbRef = useRef<HTMLDivElement>(null);
  const spriteImgRef = useRef<HTMLImageElement | null>(null);

  const spriteUrl = useMemo(() => {
    if (hasSpriteSupport) return getPreviewSpriteUrl(resourceId!);
    return null;
  }, [hasSpriteSupport, resourceId]);

  const handleThumbMouseEnter = useCallback(() => {
    if (hasSpriteSupport && !spriteError) {
      // Try sprite scrub first
      setIsHovering(true);
      if (!spriteImgRef.current && spriteUrl) {
        const img = new window.Image();
        img.onload = () => { spriteImgRef.current = img; setSpriteLoaded(true); };
        img.onerror = () => { setSpriteError(true); if (videoUrl) setIsVideoScrubbing(true); };
        img.src = spriteUrl;
      }
    } else if (videoUrl) {
      // No sprite — use video seek scrub
      setIsVideoScrubbing(true);
    }
  }, [hasSpriteSupport, spriteUrl, spriteError, videoUrl]);

  const handleThumbMouseLeave = useCallback(() => {
    setIsHovering(false);
    setIsVideoScrubbing(false);
    setScrubPercent(0);
  }, []);

  const handleThumbMouseMove = useCallback((e: React.MouseEvent) => {
    if (!thumbRef.current) return;
    const rect = thumbRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
    const pct = x / rect.width;
    if (isHovering && spriteLoaded) {
      // Sprite scrub mode
      setScrubPercent(pct);
    } else if (isVideoScrubbing) {
      // Video seek scrub mode
      setScrubPercent(pct);
      const vid = videoScrubRef.current;
      if (vid?.duration && isFinite(vid.duration)) {
        vid.currentTime = vid.duration * pct;
      }
    }
  }, [isHovering, spriteLoaded, isVideoScrubbing]);

  const spriteFrame = useMemo(() => {
    if (!spriteLoaded || !spriteImgRef.current || !thumbRef.current) return null;
    const FRAME_COUNT = 10;
    const img = spriteImgRef.current;
    const frameW = img.naturalWidth / FRAME_COUNT;
    const frameH = img.naturalHeight;
    const cW = thumbRef.current.offsetWidth;
    const cH = thumbRef.current.offsetHeight;
    if (!frameW || !frameH || !cW || !cH) return null;
    const scale = Math.min(cW / frameW, cH / frameH);
    const rfw = frameW * scale;
    const rfh = frameH * scale;
    const frameIndex = Math.min(Math.floor(scrubPercent * FRAME_COUNT), FRAME_COUNT - 1);
    return {
      style: {
        width: `${rfw}px`,
        height: `${rfh}px`,
        backgroundImage: `url(${spriteUrl})`,
        backgroundSize: `${rfw * FRAME_COUNT}px ${rfh}px`,
        backgroundPosition: `${-frameIndex * rfw}px 0px`,
        backgroundRepeat: 'no-repeat',
      } as React.CSSProperties,
    };
  }, [spriteLoaded, scrubPercent, spriteUrl]);

  const showingSpriteOverlay = isHovering && spriteLoaded && spriteFrame;
  const showingAnyScrub = showingSpriteOverlay || isVideoScrubbing;

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
      className={`group relative flex flex-col bg-zinc-900 rounded-lg overflow-hidden border transition-[background-color,box-shadow] duration-150 active:scale-[0.98] shadow-sm ${
        isChecked || isSelected
          ? 'border-indigo-500/50 ring-1 ring-inset ring-indigo-500/30'
          : 'border-zinc-800 hover:border-zinc-600'
      }`}
      onDoubleClick={onDoubleClick}
      onContextMenu={onContextMenu ? (e) => { e.preventDefault(); onContextMenu(e, data); } : undefined}
      data-context-item
    >
      {/* Checkbox overlay */}
      {selectable && (
        <div
          className={`absolute top-2 left-2 z-30 ${forceShowCheckbox || isChecked ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'} transition-opacity`}
        >
          <button
            onClick={(e) => { e.stopPropagation(); onToggleSelect?.(e); }}
            className={`w-6 h-6 rounded-full flex items-center justify-center transition-colors ${
              isChecked
                ? 'bg-indigo-500 text-white shadow-lg'
                : 'bg-black/50 border border-zinc-400 text-transparent hover:border-zinc-200'
            }`}
          >
            <Check size={12} />
          </button>
        </div>
      )}
      {/* Thumbnail Container - Sprite Scrub or Video Autoplay on Hover */}
      <div
        ref={thumbRef}
        className="relative w-full overflow-hidden bg-black aspect-[2/3] cursor-pointer"
        onClick={onClick}
        onMouseEnter={isVideo ? handleThumbMouseEnter : undefined}
        onMouseLeave={isVideo ? handleThumbMouseLeave : undefined}
        onMouseMove={isVideo ? handleThumbMouseMove : undefined}
      >
        {/* Cover image (hidden when sprite overlay active) */}
        {imageError ? (
          <div className="w-full h-full flex flex-col items-center justify-center bg-zinc-800">
            <ImageIcon size={32} className="text-zinc-600 mb-2" />
            <span className="text-xs text-zinc-500">Image unavailable</span>
          </div>
        ) : (
          <img
            src={coverUrl}
            alt={data.title}
            className={`w-full h-full object-cover transition-transform duration-500 group-hover:scale-105 opacity-90 block ${showingAnyScrub ? 'invisible' : ''}`}
            referrerPolicy="no-referrer"
            onError={() => setImageError(true)}
          />
        )}

        {/* Video seek scrub overlay */}
        {isVideoScrubbing && videoUrl && (
          <video
            ref={videoScrubRef}
            src={videoUrl}
            preload="auto"
            muted
            playsInline
            className="absolute inset-0 w-full h-full object-contain bg-black z-[5]"
          />
        )}

        {/* Sprite scrub overlay */}
        {showingSpriteOverlay && (
          <div className="absolute inset-0 bg-black flex items-center justify-center">
            <div style={spriteFrame.style} />
          </div>
        )}
        {/* Scrub progress bar */}
        {showingAnyScrub && (
          <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-black/30 z-20">
            <div className="h-full bg-white/80 transition-none" style={{ width: `${scrubPercent * 100}%` }} />
          </div>
        )}

        {/* Carousel Controls for Albums */}
        {isAlbum && !showingAnyScrub && (
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
            {/* Image count moved to right side, below type indicator */}
          </>
        )}

        {/* Platform Logo Badge */}
        {!showingAnyScrub && data.source_platform && (
          <div className="absolute top-2 left-2 z-10 pointer-events-none">
            {['douyin', 'bilibili', 'youtube', 'tiktok', 'xiaohongshu', 'twitter'].includes(data.source_platform) ? (
              <div className="bg-black/60 backdrop-blur-sm p-1 rounded-full">
                <img src={`/icons/${data.source_platform}.svg`} alt="" className="w-4 h-4" />
              </div>
            ) : (
              <span className="bg-black/70 backdrop-blur-sm px-2 py-0.5 rounded-full text-[10px] text-white/90 font-medium">
                {getPlatformLabel(data.source_platform)}
              </span>
            )}
          </div>
        )}

        {/* Type Indicator (Right side) + Album count */}
        {!showingAnyScrub && (
          <div className="absolute top-2 right-2 flex flex-col items-end gap-1 z-10 pointer-events-none">
            <div className="flex items-center gap-1.5">
              {isShared && (
                <div className="bg-indigo-500/90 backdrop-blur-sm p-1.5 rounded-full text-white" title="Shared in Team">
                  <Users size={12} />
                </div>
              )}
              <div className="bg-black/60 backdrop-blur-sm p-1.5 rounded-full text-white/90">
                {isVideo ? <VideoIcon size={12} /> : <ImageIcon size={12} />}
              </div>
            </div>
            {isAlbum && (
              <div className="bg-black/60 backdrop-blur-sm px-1.5 py-0.5 rounded-full text-[10px] text-white/90 font-medium">
                {currentImageIndex + 1}/{images.length}
              </div>
            )}
          </div>
        )}

        {/* Play Overlay (Desktop Hover - Only for Video without sprite) */}
        {isVideo && !showingAnyScrub && (
          <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-black/20 z-10 pointer-events-none">
            <div className="bg-white/20 backdrop-blur-md p-2 rounded-full">
              <Play size={20} className="text-white fill-white" />
            </div>
          </div>
        )}

        {/* Gradient Overlay */}
        {!showingAnyScrub && (
          <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent opacity-60 pointer-events-none" />
        )}

        {/* Title Overlay */}
        {!showingAnyScrub && (
          <div className="absolute bottom-0 left-0 right-0 p-2 z-10 pointer-events-none">
            <h3 className="text-[11px] text-white font-medium line-clamp-2 leading-tight drop-shadow-md">
              {data.title || 'Untitled Media'}
            </h3>
          </div>
        )}
      </div>

      {/* Info & Actions Section - Handles Detail View */}
      <div
        className="p-2 flex flex-col gap-2 bg-zinc-900 cursor-pointer"
        onClick={(e) => onClick(e)}
      >
        
        {/* Tags Row - Single line only */}
        {data.tags && data.tags.length > 0 ? (
           <div className="flex items-center gap-1 overflow-hidden">
              {data.tags.slice(0, 3).map((tag, i) => (
                 <span key={i} className={`text-[9px] px-1.5 py-0.5 rounded border font-medium whitespace-nowrap ${getTagColor(tag)}`}>
                    #{tag}
                 </span>
              ))}
              {data.tags.length > 3 && (
                 <span className="text-[9px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-500 border border-zinc-700 whitespace-nowrap">+{data.tags.length - 3}</span>
              )}
           </div>
        ) : (
           <div className="h-0" />
        )}

        {/* AI Status Icons — prefer resource status over parsed_media status */}
        <div className="flex items-center gap-1.5">
          <div title={`Transcript: ${aiStatus?.transcript_status || data.transcript_status || 'none'}`}>
            <FileText size={11} className={getAIStatusClass(aiStatus?.transcript_status || data.transcript_status)} />
          </div>
          <div title={`Summary: ${aiStatus?.summary_status || data.summary_status || 'none'}`}>
            <Sparkles size={11} className={getAIStatusClass(aiStatus?.summary_status || data.summary_status)} />
          </div>
          <div title={`Analysis: ${aiStatus?.visual_analysis_status || data.visual_analysis_status || 'none'}`}>
            <Eye size={11} className={getAIStatusClass(aiStatus?.visual_analysis_status || data.visual_analysis_status)} />
          </div>
        </div>

        {/* Stats Grid */}
        <div className="grid grid-cols-4 gap-1.5">
           {/* Like */}
           <div className="flex flex-col items-center justify-center py-1.5 bg-[#2A1818] rounded-md border border-red-900/30">
              <Heart size={12} className="text-rose-500 mb-0.5" />
              <span className="text-[9px] text-white font-bold">{formatNumber(data.like_count)}</span>
           </div>
           {/* Comment */}
           <div className="flex flex-col items-center justify-center py-1.5 bg-[#10243E] rounded-md border border-sky-900/30">
              <MessageCircle size={12} className="text-sky-500 mb-0.5" />
              <span className="text-[9px] text-white font-bold">{formatNumber(data.comment_count)}</span>
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
              className="flex flex-col items-center justify-center py-1.5 bg-[#0F291E] rounded-md border border-emerald-900/30 hover:border-emerald-500/50 hover:bg-emerald-500/10 transition-all cursor-pointer group"
              title="Click to copy link"
           >
              {copiedShare ? (
                <Check size={12} className="text-emerald-400 mb-0.5" />
              ) : (
                <Share2 size={12} className="text-emerald-500 mb-0.5 group-hover:scale-110 transition-transform" />
              )}
              <span className="text-[9px] text-white font-bold">{copiedShare ? 'Copied!' : formatNumber(data.share_count)}</span>
           </button>
           {/* Collect */}
           <div className="flex flex-col items-center justify-center py-1.5 bg-[#2E2005] rounded-md border border-amber-900/30">
              <Bookmark size={12} className="text-amber-500 mb-0.5" />
              <span className="text-[9px] text-white font-bold">{formatNumber(data.favorite_count)}</span>
           </div>
        </div>

        {/* Author Footer with Date - Inline Layout */}
        <div className="flex items-center pt-1.5 border-t border-zinc-800/50 gap-1.5">
             <div className="w-4 h-4 rounded-full bg-zinc-800 flex items-center justify-center overflow-hidden flex-shrink-0 border border-zinc-700">
                <User size={10} className="text-zinc-500" />
             </div>
             <div className="flex items-center min-w-0 flex-1">
                <span className="text-[10px] text-zinc-400 font-medium truncate">@{data.author || 'User'}</span>
                <span className="text-[10px] text-zinc-600 mx-1">·</span>
                <span className="text-[9px] text-zinc-500 font-mono flex-shrink-0">
                   {formatDate(data.published_at)}
                </span>
             </div>
        </div>
      </div>
    </div>
  );
};
