import React, { useState, useEffect, useRef, useCallback } from 'react';
import { ChevronLeft, ChevronRight, Volume2, VolumeX, Loader2, ImageOff } from 'lucide-react';
import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from '../services/parserService';

interface Slide {
  name: string;
  type: string;
  media_type: 'image' | 'video';
  url: string;
}

interface SlidePlayerProps {
  mediaId: string;
  mediaToken?: string;
  downloadStatus?: string; // 'pending' | 'downloading' | 'completed' | 'failed'
}

export const SlidePlayer: React.FC<SlidePlayerProps> = ({ mediaId, mediaToken, downloadStatus }) => {
  const [slides, setSlides] = useState<Slide[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isMuted, setIsMuted] = useState(true);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const touchStartX = useRef(0);
  const touchDeltaX = useRef(0);
  const isSwiping = useRef(false);

  const baseUrl = getApiUrl();

  // Build authenticated URL with token query param
  const buildSlideUrl = useCallback((filename: string) => {
    const url = `${baseUrl}/api/v1/media/${mediaId}/slides/${filename}`;
    return mediaToken ? `${url}?token=${encodeURIComponent(mediaToken)}` : url;
  }, [baseUrl, mediaId, mediaToken]);

  const audioUrl = `${baseUrl}/api/v1/media/${mediaId}/audio${mediaToken ? `?token=${encodeURIComponent(mediaToken)}` : ''}`;

  // Fetch slide list on mount
  useEffect(() => {
    let cancelled = false;

    const fetchSlides = async () => {
      setIsLoading(true);
      setLoadError(null);
      try {
        const headers = await getAuthHeaders();
        const resp = await fetch(
          `${baseUrl}/api/v1/media/${mediaId}/slides`,
          { headers },
        );
        if (!resp.ok) {
          throw new Error(`Failed to load slides (${resp.status})`);
        }
        const json = await resp.json();
        const data: Slide[] = Array.isArray(json) ? json : (json.slides || []);
        if (!cancelled) {
          setSlides(data);
          setCurrentIndex(0);
        }
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : 'Failed to load slides');
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    };

    fetchSlides();
    return () => { cancelled = true; };
  }, [baseUrl, mediaId]);

  // Navigate slides
  const goTo = useCallback((index: number) => {
    if (slides.length === 0) return;
    const clamped = Math.max(0, Math.min(slides.length - 1, index));
    setCurrentIndex(clamped);
  }, [slides.length]);

  const goNext = useCallback(() => goTo(currentIndex + 1), [goTo, currentIndex]);
  const goPrev = useCallback(() => goTo(currentIndex - 1), [goTo, currentIndex]);

  // Keyboard navigation
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return;

      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        goPrev();
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        goNext();
      } else if (e.key === 'm') {
        e.preventDefault();
        setIsMuted(prev => !prev);
      }
    };

    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [goNext, goPrev]);

  // Sync audio mute state
  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.muted = isMuted;
    }
  }, [isMuted]);

  // Touch swipe handlers
  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    touchStartX.current = e.touches[0].clientX;
    touchDeltaX.current = 0;
    isSwiping.current = true;
  }, []);

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    if (!isSwiping.current) return;
    touchDeltaX.current = e.touches[0].clientX - touchStartX.current;
  }, []);

  const handleTouchEnd = useCallback(() => {
    if (!isSwiping.current) return;
    isSwiping.current = false;
    const threshold = 50;
    if (touchDeltaX.current < -threshold) {
      goNext();
    } else if (touchDeltaX.current > threshold) {
      goPrev();
    }
  }, [goNext, goPrev]);

  // Loading state
  if (isLoading) {
    return (
      <div className="w-full h-full bg-zinc-950 rounded-lg flex items-center justify-center">
        <Loader2 size={32} className="animate-spin text-zinc-500" />
      </div>
    );
  }

  // Error state — distinguish between downloading and actual failure
  if (loadError) {
    const isStillDownloading = !downloadStatus || downloadStatus === 'pending' || downloadStatus === 'downloading' || downloadStatus === 'skipped';
    return (
      <div className="w-full h-full bg-zinc-950 rounded-lg flex flex-col items-center justify-center gap-3">
        {isStillDownloading ? (
          <>
            <Loader2 size={48} className="text-indigo-500 animate-spin" />
            <p className="text-zinc-300 text-sm font-medium">Downloading...</p>
            <p className="text-zinc-500 text-xs">Images are being downloaded. Please wait.</p>
          </>
        ) : downloadStatus === 'failed' ? (
          <>
            <ImageOff size={48} className="text-red-500/60" />
            <p className="text-zinc-300 text-sm font-medium">Download Failed</p>
            <p className="text-zinc-500 text-xs">Try re-downloading from the action menu.</p>
          </>
        ) : (
          <>
            <ImageOff size={48} className="text-zinc-600" />
            <p className="text-zinc-400 text-sm">{loadError}</p>
          </>
        )}
      </div>
    );
  }

  // Empty state
  if (slides.length === 0) {
    return (
      <div className="w-full h-full bg-zinc-950 rounded-lg flex flex-col items-center justify-center gap-3">
        <ImageOff size={48} className="text-zinc-600" />
        <p className="text-zinc-400 text-sm">No slides available</p>
      </div>
    );
  }

  const currentSlide = slides[currentIndex];

  if (!currentSlide) {
    return (
      <div className="w-full h-full bg-zinc-950 rounded-lg flex flex-col items-center justify-center gap-3">
        <ImageOff size={48} className="text-zinc-600" />
        <p className="text-zinc-400 text-sm">Slide not found</p>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full bg-zinc-950 rounded-lg overflow-hidden select-none"
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
    >
      {/* Slide content */}
      <div className="absolute inset-0 flex items-center justify-center">
        {currentSlide.media_type === 'video' ? (
          <video
            key={currentSlide.name}
            src={buildSlideUrl(currentSlide.name)}
            className="max-w-full max-h-full object-contain"
            autoPlay
            muted
            loop
            playsInline
          />
        ) : (
          <img
            key={currentSlide.name}
            src={buildSlideUrl(currentSlide.name)}
            alt={`Slide ${currentIndex + 1}`}
            className="max-w-full max-h-full object-contain"
            draggable={false}
          />
        )}
      </div>

      {/* Left arrow */}
      {currentIndex > 0 && (
        <button
          onClick={goPrev}
          className="absolute left-2 top-1/2 -translate-y-1/2 p-2 bg-black/50 hover:bg-black/70 text-white/80 hover:text-white rounded-full transition-colors backdrop-blur-sm"
          aria-label="Previous slide"
        >
          <ChevronLeft size={24} />
        </button>
      )}

      {/* Right arrow */}
      {currentIndex < slides.length - 1 && (
        <button
          onClick={goNext}
          className="absolute right-2 top-1/2 -translate-y-1/2 p-2 bg-black/50 hover:bg-black/70 text-white/80 hover:text-white rounded-full transition-colors backdrop-blur-sm"
          aria-label="Next slide"
        >
          <ChevronRight size={24} />
        </button>
      )}

      {/* Bottom bar: indicator dots + mute toggle */}
      <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/70 to-transparent pt-8 pb-3 px-4">
        <div className="flex items-center justify-center gap-1.5">
          {/* Indicator dots */}
          <div className="flex items-center gap-1.5 flex-1 justify-center">
            {slides.map((_, idx) => (
              <button
                key={idx}
                onClick={() => goTo(idx)}
                className={`rounded-full transition-all ${
                  idx === currentIndex
                    ? 'w-2.5 h-2.5 bg-white'
                    : 'w-1.5 h-1.5 bg-white/40 hover:bg-white/60'
                }`}
                aria-label={`Go to slide ${idx + 1}`}
              />
            ))}
          </div>

          {/* Mute/unmute toggle */}
          <button
            onClick={() => setIsMuted(prev => !prev)}
            className="p-2 bg-black/50 hover:bg-black/70 text-white/80 hover:text-white rounded-full transition-colors backdrop-blur-sm"
            aria-label={isMuted ? 'Unmute' : 'Mute'}
          >
            {isMuted ? <VolumeX size={16} /> : <Volume2 size={16} />}
          </button>
        </div>

        {/* Slide counter */}
        <p className="text-center text-xs text-white/60 mt-1.5">
          {currentIndex + 1} / {slides.length}
        </p>
      </div>

      {/* Background music audio element */}
      <audio
        ref={audioRef}
        src={audioUrl}
        loop
        autoPlay
        muted={isMuted}
      />
    </div>
  );
};

export default SlidePlayer;
