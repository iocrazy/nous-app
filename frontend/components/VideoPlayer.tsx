import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Play, Pause, Volume2, VolumeX, Maximize, SkipBack, SkipForward, Settings } from 'lucide-react';
import Hls from 'hls.js';

interface HlsLevel {
  height: number;
  width: number;
  bitrate: number;
  name?: string;
}

interface VideoPlayerProps {
  src: string;
  originalSrc?: string;  // Direct file URL (non-HLS) for "Original" quality option
  mimeType?: string;
  fps?: number;
  authToken?: string;
  onTimeUpdate: (seconds: number) => void;
  onDurationChange: (seconds: number) => void;
  playerRef: React.RefObject<HTMLVideoElement | null>;
  onToggleShortcuts?: () => void;
  commentMarkers?: Array<{ time: number; color?: string }>;
}

const QUALITY_PREF_KEY = 'mediahub_quality_pref';

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
  originalSrc,
  mimeType,
  fps = 30,
  authToken,
  onTimeUpdate,
  onDurationChange,
  playerRef,
  onToggleShortcuts,
  commentMarkers,
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
  const [playbackRate, setPlaybackRate] = useState(1);
  const [showSpeedMenu, setShowSpeedMenu] = useState(false);
  const [hlsLevels, setHlsLevels] = useState<HlsLevel[]>([]);
  const [currentHlsLevel, setCurrentHlsLevel] = useState(-1);
  const [isAutoQuality, setIsAutoQuality] = useState(true);
  const [showQualityMenu, setShowQualityMenu] = useState(false);
  const [isOriginalMode, setIsOriginalMode] = useState(false); // Playing original file directly

  const effectiveFps = fps || 30;
  const isHls = new URL(src, window.location.origin).pathname.endsWith('.m3u8');

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

  // Seek relative to current position (works in both playing and paused states)
  const seekRelative = useCallback((seconds: number) => {
    const video = playerRef.current;
    if (!video) return;
    const newTime = Math.max(0, Math.min(video.duration || 0, video.currentTime + seconds));
    video.currentTime = newTime;
    setCurrentTime(newTime);
    onTimeUpdate(newTime);
  }, [playerRef, onTimeUpdate]);

  // Change playback speed
  const changeSpeed = useCallback((rate: number) => {
    const video = playerRef.current;
    if (!video) return;
    const clamped = Math.max(0.25, Math.min(3, rate));
    video.playbackRate = clamped;
    setPlaybackRate(clamped);
    setShowSpeedMenu(false);
  }, [playerRef]);

  // Change HLS quality level (-1 = auto)
  const changeQuality = useCallback((levelIndex: number) => {
    const video = playerRef.current;
    if (!video) return;

    // If currently in original mode, re-create HLS instance
    if (isOriginalMode && isHls && Hls.isSupported()) {
      setIsOriginalMode(false);
      const hlsConfig: Partial<Hls['config']> = {};
      if (authToken) {
        hlsConfig.xhrSetup = (xhr: XMLHttpRequest) => {
          xhr.setRequestHeader('Authorization', `Bearer ${authToken}`);
        };
      }
      const pos = video.currentTime;
      const wasPlaying = !video.paused;
      const hls = new Hls(hlsConfig);
      hlsRef.current = hls;
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        hls.currentLevel = levelIndex;
        video.currentTime = pos;
        if (wasPlaying) video.play().catch(() => {});
      });
      hls.on(Hls.Events.LEVEL_SWITCHED, (_event, data) => {
        setCurrentHlsLevel(data.level);
      });
      setCurrentHlsLevel(levelIndex >= 0 ? levelIndex : -1);
      setIsAutoQuality(levelIndex === -1);
      setShowQualityMenu(false);
      return;
    }

    const hls = hlsRef.current;
    if (!hls) return;

    hls.currentLevel = levelIndex;
    setCurrentHlsLevel(levelIndex >= 0 ? levelIndex : hls.currentLevel);
    setIsAutoQuality(levelIndex === -1);
    setShowQualityMenu(false);

    // Persist quality preference by height (stable across playlist rewrites)
    if (levelIndex === -1) {
      localStorage.setItem(QUALITY_PREF_KEY, 'auto');
    } else if (levelIndex >= 0 && hlsLevels[levelIndex]) {
      localStorage.setItem(QUALITY_PREF_KEY, `${hlsLevels[levelIndex].height}p`);
    }

    // Force reload current position when paused to make switch visible
    if (video.paused && levelIndex >= 0) {
      const pos = video.currentTime;
      setTimeout(() => {
        if (video.currentTime === pos) {
          video.currentTime = pos; // trigger fragment load
        }
      }, 100);
    }
  }, [isOriginalMode, isHls, authToken, src, playerRef, hlsLevels]);

  // Switch to original (non-HLS) direct file playback
  const switchToOriginal = useCallback(() => {
    if (!originalSrc || !playerRef.current) return;
    const video = playerRef.current;
    const pos = video.currentTime;
    const wasPlaying = !video.paused;

    // Destroy HLS instance
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }

    // Switch to direct file
    video.src = originalSrc;
    video.currentTime = pos;
    if (wasPlaying) video.play().catch(() => {});

    setIsOriginalMode(true);
    setIsAutoQuality(false);
    setShowQualityMenu(false);

    // Persist quality preference
    localStorage.setItem(QUALITY_PREF_KEY, 'original');
  }, [originalSrc, playerRef]);

  // Attach or detach HLS / native source
  useEffect(() => {
    const video = playerRef.current;
    if (!video) return;

    setIsLoading(true);
    setIsPlaying(false);
    setCurrentTime(0);
    setDuration(0);
    setResolution(null);
    setHlsLevels([]);
    setCurrentHlsLevel(-1);
    setIsAutoQuality(true);

    if (isHls && Hls.isSupported()) {
      const hlsConfig: Partial<Hls['config']> = {};
      if (authToken) {
        hlsConfig.xhrSetup = (xhr: XMLHttpRequest) => {
          xhr.setRequestHeader('Authorization', `Bearer ${authToken}`);
        };
      }
      const hls = new Hls(hlsConfig);
      hlsRef.current = hls;
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, (_event, data) => {
        setIsLoading(false);
        const levels = data.levels.map((l) => ({
          height: l.height,
          width: l.width,
          bitrate: l.bitrate,
          name: (l as unknown as Record<string, unknown>).attrs
            ? ((l as unknown as Record<string, Record<string, string>>).attrs?.NAME || undefined)
            : undefined,
        }));
        setHlsLevels(levels);

        // Restore quality preference from localStorage (stored as height, e.g. "720p")
        const pref = localStorage.getItem(QUALITY_PREF_KEY);
        if (pref === 'original' && originalSrc) {
          // Will switch to original after HLS init completes
          setTimeout(() => switchToOriginal(), 0);
        } else if (pref && pref !== 'auto') {
          const matchIdx = levels.findIndex(l => `${l.height}p` === pref);
          if (matchIdx >= 0) {
            hls.currentLevel = matchIdx;
            setIsAutoQuality(false);
          }
        }
      });
      hls.on(Hls.Events.LEVEL_SWITCHED, (_event, data) => {
        setCurrentHlsLevel(data.level);
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
  }, [src, isHls, authToken, playerRef, originalSrc, switchToOriginal]);

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
    const video = playerRef.current;
    const container = containerRef.current;
    if (!container) return;

    // iOS Safari: use webkitEnterFullscreen on the video element
    if (video && typeof (video as any).webkitEnterFullscreen === 'function') {
      (video as any).webkitEnterFullscreen();
      return;
    }

    // Standard Fullscreen API
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    } else {
      container.requestFullscreen().catch(() => {});
    }
  }, [playerRef]);

  // Comprehensive keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const video = playerRef.current;
      if (!video) return;

      // Ignore if user is typing in an input/textarea
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return;

      switch (e.key) {
        case ' ':
          e.preventDefault();
          togglePlayPause();
          break;
        case 'f':
          e.preventDefault();
          handleFullscreen();
          break;
        case 'm':
          e.preventDefault();
          toggleMute();
          break;
        case 'ArrowLeft':
          e.preventDefault();
          seekRelative(-5);
          break;
        case 'ArrowRight':
          e.preventDefault();
          seekRelative(5);
          break;
        case ',':
          e.preventDefault();
          if (video.paused) stepFrame(-1, 1);
          break;
        case '.':
          e.preventDefault();
          if (video.paused) stepFrame(1, 1);
          break;
        case '<':
          e.preventDefault();
          if (video.paused) stepFrame(-1, 10);
          break;
        case '>':
          e.preventDefault();
          if (video.paused) stepFrame(1, 10);
          break;
        case '[':
          e.preventDefault();
          changeSpeed(playbackRate - 0.25);
          break;
        case ']':
          e.preventDefault();
          changeSpeed(playbackRate + 0.25);
          break;
        case '\\':
          e.preventDefault();
          changeSpeed(1);
          break;
        case '?':
          e.preventDefault();
          onToggleShortcuts?.();
          break;
        default:
          break;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [playerRef, stepFrame, seekRelative, togglePlayPause, handleFullscreen, toggleMute, changeSpeed, playbackRate, onToggleShortcuts]);

  const seekProgress = duration > 0 ? (currentTime / duration) * 100 : 0;
  const frameNumber = Math.floor((currentTime || 0) * effectiveFps);

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full bg-black rounded-lg overflow-hidden group"
      onMouseMove={resetHideTimer}
      onMouseLeave={() => {
        if (isPlaying) setShowControls(false);
      }}
    >
      {/* Video element */}
      <video
        ref={playerRef}
        className="absolute inset-0 w-full h-full object-contain cursor-pointer"
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
            {/* Comment timeline markers */}
            {commentMarkers && duration > 0 && commentMarkers.map((marker, idx) => (
              <div
                key={idx}
                className="absolute top-1/2 -translate-y-1/2 w-1.5 h-1.5 rounded-full pointer-events-none"
                style={{
                  left: `${Math.min(100, (marker.time / duration) * 100)}%`,
                  backgroundColor: marker.color || '#818cf8',
                }}
              />
            ))}
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
              title="Previous Frame (,)"
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
              title="Next Frame (.)"
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

          {/* Speed control */}
          <div className="relative">
            {showSpeedMenu && (
              <div
                className="fixed inset-0 z-10"
                onClick={() => setShowSpeedMenu(false)}
              />
            )}
            <button
              onClick={() => setShowSpeedMenu(!showSpeedMenu)}
              className="px-2 py-1 text-xs font-mono text-zinc-300 hover:text-white hover:bg-white/10 rounded transition-colors"
              title="Playback Speed"
            >
              {playbackRate === 1 ? '1.0x' : `${playbackRate}x`}
            </button>
            {showSpeedMenu && (
              <div className="absolute bottom-full right-0 mb-2 bg-zinc-900/95 backdrop-blur-sm border border-zinc-700 rounded-lg shadow-xl py-1 min-w-[80px] z-20">
                {[0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3].map((rate) => (
                  <button
                    key={rate}
                    onClick={() => changeSpeed(rate)}
                    className={`w-full px-3 py-1.5 text-xs text-left transition-colors ${
                      playbackRate === rate
                        ? 'text-indigo-400 bg-indigo-500/10'
                        : 'text-zinc-300 hover:bg-zinc-800 hover:text-white'
                    }`}
                  >
                    {rate}x
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Quality selector (HLS) or static resolution label */}
          {(hlsLevels.length > 1 || (hlsLevels.length > 0 && originalSrc)) ? (
            <div className="relative">
              {showQualityMenu && (
                <div
                  className="fixed inset-0 z-10"
                  onClick={() => setShowQualityMenu(false)}
                />
              )}
              <button
                onClick={() => setShowQualityMenu(!showQualityMenu)}
                className="px-2 py-1 text-xs font-mono text-zinc-300 hover:text-white hover:bg-white/10 rounded transition-colors cursor-pointer"
                title="Quality"
              >
                {isOriginalMode
                  ? 'Original'
                  : isAutoQuality
                    ? `Auto${currentHlsLevel >= 0 && hlsLevels[currentHlsLevel]
                        ? ` (${hlsLevels[currentHlsLevel].height}p · ${Math.round(hlsLevels[currentHlsLevel].bitrate / 1000)}k)`
                        : ''}`
                    : currentHlsLevel >= 0 && hlsLevels[currentHlsLevel]
                      ? `${hlsLevels[currentHlsLevel].height}p`
                      : 'Auto'}
              </button>
              {showQualityMenu && (
                <div className="absolute bottom-full right-0 mb-2 bg-zinc-900/95 backdrop-blur-sm border border-zinc-700 rounded-lg shadow-xl py-1 min-w-[100px] z-20">
                  <button
                    onClick={() => changeQuality(-1)}
                    className={`w-full px-3 py-1.5 text-xs text-left transition-colors ${
                      isAutoQuality && !isOriginalMode
                        ? 'text-indigo-400 bg-indigo-500/10'
                        : 'text-zinc-300 hover:bg-zinc-800 hover:text-white'
                    }`}
                  >
                    Auto
                  </button>
                  {hlsLevels
                    .map((level, idx) => ({ level, idx }))
                    .filter(({ level }) => level.name !== 'Original')
                    .sort((a, b) => b.level.bitrate - a.level.bitrate)
                    .map(({ level, idx }) => (
                      <button
                        key={idx}
                        onClick={() => changeQuality(idx)}
                        className={`w-full px-3 py-1.5 text-xs text-left transition-colors ${
                          !isAutoQuality && !isOriginalMode && currentHlsLevel === idx
                            ? 'text-indigo-400 bg-indigo-500/10'
                            : 'text-zinc-300 hover:bg-zinc-800 hover:text-white'
                        }`}
                      >
                        {level.height}p
                      </button>
                    ))}
                  {originalSrc && (
                    <button
                      onClick={switchToOriginal}
                      className={`w-full px-3 py-1.5 text-xs text-left transition-colors ${
                        isOriginalMode
                          ? 'text-indigo-400 bg-indigo-500/10'
                          : 'text-zinc-300 hover:bg-zinc-800 hover:text-white'
                      }`}
                    >
                      Original
                    </button>
                  )}
                </div>
              )}
            </div>
          ) : resolution ? (
            <span className="px-2 py-1 text-xs font-mono text-zinc-400 select-none">
              {resolution.height >= 2160 ? '4K' : resolution.height >= 1080 ? '1080p' : resolution.height >= 720 ? '720p' : `${resolution.height}p`}
            </span>
          ) : null}

          {/* Settings — opens shortcuts help */}
          {onToggleShortcuts && (
            <button
              onClick={onToggleShortcuts}
              className="p-1.5 text-zinc-400 hover:text-white transition-colors"
              aria-label="Keyboard Shortcuts"
              title="Keyboard Shortcuts (?)"
            >
              <Settings size={16} />
            </button>
          )}

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
