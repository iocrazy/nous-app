import React, { useRef, useState, useEffect, useCallback } from 'react';
import { Play, Pause, Volume2, VolumeX, Maximize, SkipBack, SkipForward } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { FileVersion } from '../types';
import Hls from 'hls.js';

interface VersionCompareViewProps {
  projectId: string;
  fileId: string;
  versionA: FileVersion;
  versionB: FileVersion;
  fps?: number;
  getVideoSrc: (version: FileVersion) => string;
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

function useVideoSetup(
  videoRef: React.RefObject<HTMLVideoElement | null>,
  src: string,
) {
  const hlsRef = useRef<Hls | null>(null);
  const isHls = src.endsWith('.m3u8');

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    if (isHls && Hls.isSupported()) {
      const hls = new Hls();
      hlsRef.current = hls;
      hls.loadSource(src);
      hls.attachMedia(video);
    } else {
      video.src = src;
    }

    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
    };
  }, [src, isHls, videoRef]);
}

export const VersionCompareView: React.FC<VersionCompareViewProps> = ({
  projectId,
  fileId,
  versionA,
  versionB,
  fps = 30,
  getVideoSrc,
}) => {
  const { t } = useTranslation();
  const videoARef = useRef<HTMLVideoElement>(null);
  const videoBRef = useRef<HTMLVideoElement>(null);
  const syncAnimFrameRef = useRef<number>(0);
  const containerRef = useRef<HTMLDivElement>(null);

  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [durationA, setDurationA] = useState(0);
  const [durationB, setDurationB] = useState(0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [loadedA, setLoadedA] = useState(false);
  const [loadedB, setLoadedB] = useState(false);

  const effectiveFps = fps || 30;
  const srcA = getVideoSrc(versionA);
  const srcB = getVideoSrc(versionB);

  // Setup HLS/native sources
  useVideoSetup(videoARef, srcA);
  useVideoSetup(videoBRef, srcB);

  // Track loaded state and duration
  useEffect(() => {
    const vA = videoARef.current;
    const vB = videoBRef.current;
    if (!vA || !vB) return;

    const onLoadedA = () => {
      setLoadedA(true);
      if (isFinite(vA.duration)) setDurationA(vA.duration);
    };
    const onLoadedB = () => {
      setLoadedB(true);
      if (isFinite(vB.duration)) setDurationB(vB.duration);
    };

    vA.addEventListener('loadedmetadata', onLoadedA);
    vB.addEventListener('loadedmetadata', onLoadedB);

    return () => {
      vA.removeEventListener('loadedmetadata', onLoadedA);
      vB.removeEventListener('loadedmetadata', onLoadedB);
    };
  }, [srcA, srcB]);

  // Compute shared duration as the shorter of the two
  useEffect(() => {
    if (durationA > 0 && durationB > 0) {
      setDuration(Math.min(durationA, durationB));
    } else if (durationA > 0) {
      setDuration(durationA);
    } else if (durationB > 0) {
      setDuration(durationB);
    }
  }, [durationA, durationB]);

  // Sync loop using requestAnimationFrame
  const startSyncLoop = useCallback(() => {
    const sync = () => {
      const vA = videoARef.current;
      const vB = videoBRef.current;
      if (!vA || !vB) return;

      // Use video A as the master
      const masterTime = vA.currentTime;
      setCurrentTime(masterTime);

      // Sync B to A if drift > half a frame
      const frameTolerance = 0.5 / effectiveFps;
      if (Math.abs(vB.currentTime - masterTime) > frameTolerance) {
        vB.currentTime = masterTime;
      }

      if (!vA.paused) {
        syncAnimFrameRef.current = requestAnimationFrame(sync);
      }
    };
    syncAnimFrameRef.current = requestAnimationFrame(sync);
  }, [effectiveFps]);

  const stopSyncLoop = useCallback(() => {
    if (syncAnimFrameRef.current) {
      cancelAnimationFrame(syncAnimFrameRef.current);
      syncAnimFrameRef.current = 0;
    }
  }, []);

  // Clean up sync loop on unmount
  useEffect(() => {
    return () => stopSyncLoop();
  }, [stopSyncLoop]);

  // Synced play/pause
  const togglePlayPause = useCallback(() => {
    const vA = videoARef.current;
    const vB = videoBRef.current;
    if (!vA || !vB) return;

    if (vA.paused) {
      // Sync B to A before playing
      vB.currentTime = vA.currentTime;
      vA.play().catch(() => {});
      vB.play().catch(() => {});
      setIsPlaying(true);
      startSyncLoop();
    } else {
      vA.pause();
      vB.pause();
      setIsPlaying(false);
      stopSyncLoop();
      // Final sync
      setCurrentTime(vA.currentTime);
      vB.currentTime = vA.currentTime;
    }
  }, [startSyncLoop, stopSyncLoop]);

  // Synced seek
  const handleSeek = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const time = parseFloat(e.target.value);
    const vA = videoARef.current;
    const vB = videoBRef.current;
    if (!vA || !vB) return;

    vA.currentTime = time;
    vB.currentTime = time;
    setCurrentTime(time);
  }, []);

  // Frame stepping (both videos synced)
  const stepFrame = useCallback((direction: 1 | -1, count: number = 1) => {
    const vA = videoARef.current;
    const vB = videoBRef.current;
    if (!vA || !vB || !vA.paused) return;

    const frameDuration = 1 / effectiveFps;
    const newTime = Math.max(0, Math.min(duration, vA.currentTime + direction * count * frameDuration));
    vA.currentTime = newTime;
    vB.currentTime = newTime;
    setCurrentTime(newTime);
  }, [effectiveFps, duration]);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const vA = videoARef.current;
      if (!vA || !vA.paused) return;

      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return;

      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        stepFrame(-1, e.shiftKey ? 10 : 1);
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        stepFrame(1, e.shiftKey ? 10 : 1);
      } else if (e.key === ' ') {
        e.preventDefault();
        togglePlayPause();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [stepFrame, togglePlayPause]);

  // Volume control
  const handleVolumeChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const vol = parseFloat(e.target.value);
    const vA = videoARef.current;
    const vB = videoBRef.current;
    if (vA) { vA.volume = vol; vA.muted = vol === 0; }
    if (vB) { vB.volume = vol; vB.muted = vol === 0; }
    setVolume(vol);
    setIsMuted(vol === 0);
  }, []);

  const toggleMute = useCallback(() => {
    const newMuted = !isMuted;
    const vA = videoARef.current;
    const vB = videoBRef.current;
    if (vA) vA.muted = newMuted;
    if (vB) vB.muted = newMuted;
    setIsMuted(newMuted);
  }, [isMuted]);

  const handleFullscreen = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    } else {
      container.requestFullscreen().catch(() => {});
    }
  }, []);

  // Handle end of video
  useEffect(() => {
    const vA = videoARef.current;
    if (!vA) return;

    const handleEnded = () => {
      const vB = videoBRef.current;
      if (vB) vB.pause();
      setIsPlaying(false);
      stopSyncLoop();
    };

    vA.addEventListener('ended', handleEnded);
    return () => vA.removeEventListener('ended', handleEnded);
  }, [stopSyncLoop]);

  const seekProgress = duration > 0 ? (currentTime / duration) * 100 : 0;
  const frameNumber = Math.floor((currentTime || 0) * effectiveFps);
  const bothLoaded = loadedA && loadedB;

  return (
    <div ref={containerRef} className="flex flex-col h-full bg-black">
      {/* Side-by-side video area */}
      <div className="flex-1 min-h-0 flex flex-col md:flex-row gap-0.5 p-0.5">
        {/* Version A */}
        <div className="flex-1 min-w-0 flex flex-col relative">
          <div className="absolute top-2 left-2 z-10 px-2.5 py-1 bg-black/70 backdrop-blur-sm rounded-lg text-xs font-medium text-blue-300 border border-blue-500/30">
            V{versionA.version_number}
          </div>
          <div className="flex-1 min-h-0 flex items-center justify-center bg-ink-950 rounded-sm overflow-hidden">
            <video
              ref={videoARef}
              className="w-full h-full object-contain"
              playsInline
              preload="metadata"
              muted={isMuted}
            />
          </div>
        </div>

        {/* Version B */}
        <div className="flex-1 min-w-0 flex flex-col relative">
          <div className="absolute top-2 left-2 z-10 px-2.5 py-1 bg-black/70 backdrop-blur-sm rounded-lg text-xs font-medium text-emerald-300 border border-emerald-500/30">
            V{versionB.version_number}
          </div>
          <div className="flex-1 min-h-0 flex items-center justify-center bg-ink-950 rounded-sm overflow-hidden">
            <video
              ref={videoBRef}
              className="w-full h-full object-contain"
              playsInline
              preload="metadata"
              muted
            />
          </div>
        </div>
      </div>

      {/* Loading indicator */}
      {!bothLoaded && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/50 pointer-events-none z-20">
          <div className="w-8 h-8 border-2 border-ink-600 border-t-white rounded-full animate-spin" />
        </div>
      )}

      {/* Unified controls bar */}
      <div className="flex-shrink-0 bg-ink-900/95 backdrop-blur border-t border-ink-800 px-3 py-2">
        {/* Seek bar */}
        <div className="relative w-full h-5 flex items-center mb-1 group/seek">
          <input
            type="range"
            min={0}
            max={duration || 0}
            step={0.01}
            value={currentTime}
            onChange={handleSeek}
            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer z-10"
          />
          <div className="w-full h-1 group-hover/seek:h-1.5 bg-ink-700 rounded-full transition-all relative">
            <div
              className="h-full bg-white rounded-full transition-all relative"
              style={{ width: `${seekProgress}%` }}
            >
              <div className="absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full opacity-0 group-hover/seek:opacity-100 transition-opacity shadow-md" />
            </div>
          </div>
        </div>

        {/* Controls row */}
        <div className="flex items-center gap-1.5">
          {/* Frame step backward */}
          {!isPlaying && (
            <button
              onClick={() => stepFrame(-1)}
              className="p-1.5 text-ink-400 hover:text-white transition-colors"
              aria-label="Previous Frame"
              title="Previous Frame (Left Arrow)"
            >
              <SkipBack size={14} />
            </button>
          )}

          {/* Play / Pause */}
          <button
            onClick={togglePlayPause}
            className="p-1.5 text-white hover:text-ink-300 transition-colors"
            aria-label={isPlaying ? 'Pause' : 'Play'}
            disabled={!bothLoaded}
          >
            {isPlaying ? <Pause size={18} fill="currentColor" /> : <Play size={18} fill="currentColor" />}
          </button>

          {/* Frame step forward */}
          {!isPlaying && (
            <button
              onClick={() => stepFrame(1)}
              className="p-1.5 text-ink-400 hover:text-white transition-colors"
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
              className="p-1.5 text-white hover:text-ink-300 transition-colors"
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

          {/* Time display */}
          <span className="text-xs font-mono text-ink-300 select-none ml-1">
            {isPlaying ? (
              <>
                {formatTime(currentTime)} / {formatTime(duration)}
              </>
            ) : (
              <>
                {formatTimeWithFrames(currentTime, effectiveFps)}{' '}
                <span className="text-ink-500">[F{frameNumber}]</span>
                {' / '}
                {formatTime(duration)}
              </>
            )}
          </span>

          <div className="flex-1" />

          {/* Synced badge */}
          <span className="text-[10px] text-ink-500 font-medium px-2 py-0.5 bg-ink-800 rounded-full select-none">
            {t('mediatrack.review.syncedPlayback')}
          </span>

          {/* Fullscreen */}
          <button
            onClick={handleFullscreen}
            className="p-1.5 text-white hover:text-ink-300 transition-colors"
            aria-label="Fullscreen"
          >
            <Maximize size={16} />
          </button>
        </div>
      </div>
    </div>
  );
};

export default VersionCompareView;
