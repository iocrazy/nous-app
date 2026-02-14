import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Play, Pause, Volume2, VolumeX, Maximize, SkipBack, SkipForward } from 'lucide-react';
import Hls from 'hls.js';

interface VideoPlayerProps {
  src: string;
  mimeType?: string;
  fps?: number;
  onTimeUpdate: (seconds: number) => void;
  onDurationChange: (seconds: number) => void;
  playerRef: React.RefObject<HTMLVideoElement | null>;
}

const formatTime = (seconds: number): string => {
  if (!isFinite(seconds) || isNaN(seconds)) return '00:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
};

const formatTimeWithFrames = (seconds: number, fps: number): string => {
  if (!isFinite(seconds) || isNaN(seconds)) return '00:00.00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const fractional = seconds % 1;
  const frameInSecond = Math.floor(fractional * fps);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${frameInSecond.toString().padStart(2, '0')}`;
};

export const VideoPlayer: React.FC<VideoPlayerProps> = ({
  src,
  mimeType,
  fps = 30,
  onTimeUpdate,
  onDurationChange,
  playerRef,
}) => {
  const hlsRef = useRef<Hls | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const hideControlsTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [showControls, setShowControls] = useState(true);
  const [resolution, setResolution] = useState<{ width: number; height: number } | null>(null);

  const effectiveFps = fps || 30;
  const isHls = src.endsWith('.m3u8');

  // Frame stepping
  const stepFrame = useCallback((direction: 1 | -1, count: number = 1) => {
    if (!playerRef.current) return;
    const video = playerRef.current;
    if (!video.paused) return;
    const frameDuration = 1 / effectiveFps;
    const newTime = Math.max(0, Math.min(video.duration, video.currentTime + direction * count * frameDuration));
    video.currentTime = newTime;
    setCurrentTime(newTime);
    onTimeUpdate(newTime);
  }, [playerRef, effectiveFps, onTimeUpdate]);

  // Attach or detach HLS / native source
  useEffect(() => {
    const video = playerRef.current;
    if (!video) return;

    setIsLoading(true);
    setIsPlaying(false);
    setCurrentTime(0);
    setDuration(0);
    setResolution(null);

    if (isHls && Hls.isSupported()) {
      const hls = new Hls();
      hlsRef.current = hls;
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        setIsLoading(false);
      });
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) {
          setIsLoading(false);
        }
      });
    } else if (isHls && video.canPlayType('application/vnd.apple.mpegurl')) {
      // Native HLS support (Safari)
      video.src = src;
    } else {
      video.src = src;
    }

    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
    };
  }, [src, isHls, playerRef]);

  // Video event listeners
  useEffect(() => {
    const video = playerRef.current;
    if (!video) return;

    const handleTimeUpdate = () => {
      const t = video.currentTime;
      setCurrentTime(t);
      onTimeUpdate(t);
    };

    const handleDurationChange = () => {
      const d = video.duration;
      if (isFinite(d)) {
        setDuration(d);
        onDurationChange(d);
      }
    };

    const handleLoadedMetadata = () => {
      setIsLoading(false);
      if (video.videoWidth && video.videoHeight) {
        setResolution({ width: video.videoWidth, height: video.videoHeight });
      }
      if (isFinite(video.duration)) {
        setDuration(video.duration);
        onDurationChange(video.duration);
      }
    };

    const handlePlay = () => setIsPlaying(true);
    const handlePause = () => setIsPlaying(false);
    const handleEnded = () => setIsPlaying(false);
    const handleWaiting = () => setIsLoading(true);
    const handleCanPlay = () => setIsLoading(false);

    const handleResize = () => {
      if (video.videoWidth && video.videoHeight) {
        setResolution({ width: video.videoWidth, height: video.videoHeight });
      }
    };

    video.addEventListener('timeupdate', handleTimeUpdate);
    video.addEventListener('durationchange', handleDurationChange);
    video.addEventListener('loadedmetadata', handleLoadedMetadata);
    video.addEventListener('play', handlePlay);
    video.addEventListener('pause', handlePause);
    video.addEventListener('ended', handleEnded);
    video.addEventListener('waiting', handleWaiting);
    video.addEventListener('canplay', handleCanPlay);
    video.addEventListener('resize', handleResize);

    return () => {
      video.removeEventListener('timeupdate', handleTimeUpdate);
      video.removeEventListener('durationchange', handleDurationChange);
      video.removeEventListener('loadedmetadata', handleLoadedMetadata);
      video.removeEventListener('play', handlePlay);
      video.removeEventListener('pause', handlePause);
      video.removeEventListener('ended', handleEnded);
      video.removeEventListener('waiting', handleWaiting);
      video.removeEventListener('canplay', handleCanPlay);
      video.removeEventListener('resize', handleResize);
    };
  }, [playerRef, onTimeUpdate, onDurationChange]);

  // Keyboard shortcuts for frame stepping
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const video = playerRef.current;
      if (!video || !video.paused) return;

      // Ignore if user is typing in an input/textarea
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return;

      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        stepFrame(-1, e.shiftKey ? 10 : 1);
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        stepFrame(1, e.shiftKey ? 10 : 1);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [playerRef, stepFrame]);

  // Auto-hide controls
  const resetHideTimer = useCallback(() => {
    setShowControls(true);
    if (hideControlsTimer.current) {
      clearTimeout(hideControlsTimer.current);
    }
    if (isPlaying) {
      hideControlsTimer.current = setTimeout(() => {
        setShowControls(false);
      }, 3000);
    }
  }, [isPlaying]);

  useEffect(() => {
    if (!isPlaying) {
      setShowControls(true);
      if (hideControlsTimer.current) {
        clearTimeout(hideControlsTimer.current);
      }
    } else {
      resetHideTimer();
    }
    return () => {
      if (hideControlsTimer.current) {
        clearTimeout(hideControlsTimer.current);
      }
    };
  }, [isPlaying, resetHideTimer]);

  const togglePlayPause = useCallback(() => {
    const video = playerRef.current;
    if (!video) return;
    if (video.paused) {
      video.play().catch(() => {});
    } else {
      video.pause();
    }
  }, [playerRef]);

  const handleSeek = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const video = playerRef.current;
      if (!video) return;
      const time = parseFloat(e.target.value);
      video.currentTime = time;
      setCurrentTime(time);
    },
    [playerRef],
  );

  const handleVolumeChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const video = playerRef.current;
      if (!video) return;
      const vol = parseFloat(e.target.value);
      video.volume = vol;
      setVolume(vol);
      if (vol === 0) {
        setIsMuted(true);
        video.muted = true;
      } else if (isMuted) {
        setIsMuted(false);
        video.muted = false;
      }
    },
    [playerRef, isMuted],
  );

  const toggleMute = useCallback(() => {
    const video = playerRef.current;
    if (!video) return;
    const newMuted = !isMuted;
    video.muted = newMuted;
    setIsMuted(newMuted);
  }, [playerRef, isMuted]);

  const handleFullscreen = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    } else {
      container.requestFullscreen().catch(() => {});
    }
  }, []);

  const seekProgress = duration > 0 ? (currentTime / duration) * 100 : 0;
  const frameNumber = Math.floor((currentTime || 0) * effectiveFps);

  return (
    <div
      ref={containerRef}
      className="relative w-full bg-black rounded-lg overflow-hidden group"
      onMouseMove={resetHideTimer}
      onMouseLeave={() => {
        if (isPlaying) setShowControls(false);
      }}
    >
      {/* Video element */}
      <video
        ref={playerRef}
        className="w-full h-full object-contain cursor-pointer"
        onClick={togglePlayPause}
        playsInline
        preload="metadata"
      />

      {/* Loading spinner */}
      {isLoading && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/40 pointer-events-none">
          <div className="w-10 h-10 border-3 border-zinc-600 border-t-white rounded-full animate-spin" />
        </div>
      )}

      {/* Resolution badge */}
      {resolution && showControls && (
        <div className="absolute top-3 right-3 px-2 py-0.5 bg-black/60 backdrop-blur-sm rounded text-xs font-mono text-zinc-300 pointer-events-none">
          {resolution.width}x{resolution.height}
          {resolution.height >= 2160 && (
            <span className="ml-1 text-amber-400 font-semibold">4K</span>
          )}
          {resolution.height >= 1080 && resolution.height < 2160 && (
            <span className="ml-1 text-emerald-400 font-semibold">HD</span>
          )}
        </div>
      )}

      {/* Controls overlay */}
      <div
        className={`absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/90 via-black/50 to-transparent pt-10 pb-2 px-3 transition-opacity duration-300 ${
          showControls ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
      >
        {/* Seek bar */}
        <div className="relative w-full h-5 flex items-center mb-1 group/seek">
          <input
            type="range"
            min={0}
            max={duration || 0}
            step={0.1}
            value={currentTime}
            onChange={handleSeek}
            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer z-10"
          />
          <div className="w-full h-1 group-hover/seek:h-1.5 bg-zinc-700 rounded-full transition-all relative">
            <div
              className="h-full bg-white rounded-full transition-all relative"
              style={{ width: `${seekProgress}%` }}
            >
              <div className="absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full opacity-0 group-hover/seek:opacity-100 transition-opacity shadow-md" />
            </div>
          </div>
        </div>

        {/* Bottom controls row */}
        <div className="flex items-center gap-1.5">
          {/* Frame step backward - only when paused */}
          {!isPlaying && (
            <button
              onClick={() => stepFrame(-1)}
              className="p-1.5 text-zinc-400 hover:text-white transition-colors"
              aria-label="Previous Frame"
              title="Previous Frame (Left Arrow)"
            >
              <SkipBack size={14} />
            </button>
          )}

          {/* Play / Pause */}
          <button
            onClick={togglePlayPause}
            className="p-1.5 text-white hover:text-zinc-300 transition-colors"
            aria-label={isPlaying ? 'Pause' : 'Play'}
          >
            {isPlaying ? <Pause size={18} fill="currentColor" /> : <Play size={18} fill="currentColor" />}
          </button>

          {/* Frame step forward - only when paused */}
          {!isPlaying && (
            <button
              onClick={() => stepFrame(1)}
              className="p-1.5 text-zinc-400 hover:text-white transition-colors"
              aria-label="Next Frame"
              title="Next Frame (Right Arrow)"
            >
              <SkipForward size={14} />
            </button>
          )}

          {/* Volume */}
          <div className="flex items-center gap-1 group/vol">
            <button
              onClick={toggleMute}
              className="p-1.5 text-white hover:text-zinc-300 transition-colors"
              aria-label={isMuted ? 'Unmute' : 'Mute'}
            >
              {isMuted || volume === 0 ? <VolumeX size={16} /> : <Volume2 size={16} />}
            </button>
            <div className="w-0 group-hover/vol:w-20 overflow-hidden transition-all duration-200">
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={isMuted ? 0 : volume}
                onChange={handleVolumeChange}
                className="w-20 h-1 accent-white cursor-pointer"
              />
            </div>
          </div>

          {/* Time display with frame number */}
          <span className="text-xs font-mono text-zinc-300 select-none ml-1">
            {isPlaying ? (
              <>
                {formatTime(currentTime)} / {formatTime(duration)}
              </>
            ) : (
              <>
                {formatTimeWithFrames(currentTime, effectiveFps)}{' '}
                <span className="text-zinc-500">[F{frameNumber}]</span>
                {' / '}
                {formatTime(duration)}
              </>
            )}
          </span>

          {/* Spacer */}
          <div className="flex-1" />

          {/* Fullscreen */}
          <button
            onClick={handleFullscreen}
            className="p-1.5 text-white hover:text-zinc-300 transition-colors"
            aria-label="Fullscreen"
          >
            <Maximize size={16} />
          </button>
        </div>
      </div>
    </div>
  );
};

export default VideoPlayer;
