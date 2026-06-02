import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Play, Pause, Volume2, VolumeX } from 'lucide-react';
import type { SodaTheme } from '../utils/sodaTheme';

interface AudioWaveformPlayerProps {
  src: string;
  filename: string;
  duration?: number;
  /** Emits the playback position (seconds) on every time update / frame. */
  onTimeUpdate?: (seconds: number) => void;
  /** Chorus / highlight marker position (seconds) overlaid on the waveform. */
  chorusStartSec?: number;
  /** Track's own Soda palette. When absent, neutral non-blue fallbacks apply. */
  theme?: SodaTheme;
}

// Neutral, non-blue defaults used when no track theme is supplied.
const NEUTRAL_PLAYED = '#e4e4e7'; // zinc-200
const NEUTRAL_PLAYED_DIM = 'rgba(228,228,231,0.6)';
const NEUTRAL_UNPLAYED = 'rgba(113,113,122,0.5)'; // zinc-500
const NEUTRAL_CURSOR = 'rgba(244,244,245,0.85)'; // zinc-100

const BAR_COUNT = 200;

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export const AudioWaveformPlayer: React.FC<AudioWaveformPlayerProps> = ({
  src,
  filename,
  duration: initialDuration,
  onTimeUpdate,
  chorusStartSec,
  theme,
}) => {
  const audioRef = useRef<HTMLAudioElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const animRef = useRef<number>(0);

  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(initialDuration || 0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [waveform, setWaveform] = useState<number[]>([]);
  const [isDecoding, setIsDecoding] = useState(true);
  const [isSeeking, setIsSeeking] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);

  // Decode audio to extract waveform data
  useEffect(() => {
    let cancelled = false;
    setIsDecoding(true);
    setWaveform([]);
    setCurrentTime(0);
    setIsPlaying(false);

    const decodeAudio = async () => {
      try {
        const response = await fetch(src);
        const arrayBuffer = await response.arrayBuffer();
        const audioCtx = new AudioContext();
        const decoded = await audioCtx.decodeAudioData(arrayBuffer);
        audioCtx.close();

        if (cancelled) return;

        const channel = decoded.getChannelData(0);
        const blockSize = Math.floor(channel.length / BAR_COUNT);
        const bars: number[] = [];

        for (let i = 0; i < BAR_COUNT; i++) {
          let sum = 0;
          const start = i * blockSize;
          const end = Math.min(start + blockSize, channel.length);
          for (let j = start; j < end; j++) {
            sum += Math.abs(channel[j]);
          }
          bars.push(sum / (end - start));
        }

        const max = Math.max(...bars);
        const normalized = max > 0 ? bars.map((b) => b / max) : bars;

        setWaveform(normalized);
        setDuration(decoded.duration);
        setIsDecoding(false);
      } catch (err) {
        console.error('Audio decode failed:', err);
        setIsDecoding(false);
      }
    };

    decodeAudio();
    return () => { cancelled = true; };
  }, [src]);

  // Draw waveform on canvas
  const drawWaveform = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container || waveform.length === 0) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = container.getBoundingClientRect();
    const W = rect.width;
    const H = rect.height;

    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = `${W}px`;
    canvas.style.height = `${H}px`;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);

    const progress = duration > 0 ? currentTime / duration : 0;
    const centerY = H / 2;
    const maxBarH = (H - 32) / 2; // leave margin
    const barW = W / BAR_COUNT;
    const gap = Math.max(0.5, barW * 0.15); // gap between bars

    // Colors — driven by the track's own Soda palette, neutral non-blue fallback.
    const playedColor = theme?.accent ?? NEUTRAL_PLAYED;
    const playedDimColor = theme?.accentSoft ?? NEUTRAL_PLAYED_DIM;
    const unplayedColor = theme?.waveUnplayed ?? NEUTRAL_UNPLAYED;
    const unplayedDimColor = theme?.waveUnplayed ?? 'rgba(82, 82, 91, 0.35)'; // slightly dim mirror

    for (let i = 0; i < waveform.length; i++) {
      const amp = waveform[i];
      const x = i * barW;
      const barH = Math.max(1, amp * maxBarH);
      const isPlayed = i / BAR_COUNT < progress;

      // Main color based on played state
      const mainColor = isPlayed ? playedColor : unplayedColor;
      const dimColor = isPlayed ? playedDimColor : unplayedDimColor;

      // Draw upper half
      ctx.fillStyle = mainColor;
      ctx.fillRect(x + gap / 2, centerY - barH, barW - gap, barH);

      // Draw lower half (slightly dimmer mirror)
      ctx.save();
      ctx.globalAlpha = isPlayed ? 1 : 0.7;
      ctx.fillStyle = dimColor;
      ctx.fillRect(x + gap / 2, centerY, barW - gap, barH * 0.8);
      ctx.restore();
    }

    // Center line
    ctx.strokeStyle = 'rgba(63, 63, 70, 0.5)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, centerY);
    ctx.lineTo(W, centerY);
    ctx.stroke();

    // Playback cursor
    if (duration > 0) {
      const cursorX = progress * W;
      ctx.strokeStyle = theme?.lyricActive ?? theme?.accent ?? NEUTRAL_CURSOR;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(cursorX, 8);
      ctx.lineTo(cursorX, H - 8);
      ctx.stroke();
    }
  }, [waveform, currentTime, duration, theme]);

  // Keep the latest onTimeUpdate in a ref so the rAF loop never re-subscribes.
  const onTimeUpdateRef = useRef(onTimeUpdate);
  useEffect(() => {
    onTimeUpdateRef.current = onTimeUpdate;
  }, [onTimeUpdate]);

  // Animation loop for smooth updates
  useEffect(() => {
    const tick = () => {
      if (audioRef.current) {
        const t = audioRef.current.currentTime;
        setCurrentTime(t);
        onTimeUpdateRef.current?.(t);
      }
      animRef.current = requestAnimationFrame(tick);
    };

    if (isPlaying) {
      animRef.current = requestAnimationFrame(tick);
    }

    return () => cancelAnimationFrame(animRef.current);
  }, [isPlaying]);

  // Redraw when waveform, time, or size changes
  useEffect(() => {
    drawWaveform();
  }, [drawWaveform]);

  // Resize observer
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = new ResizeObserver(() => drawWaveform());
    observer.observe(container);
    return () => observer.disconnect();
  }, [drawWaveform]);

  // Play/pause
  const togglePlay = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;

    if (isPlaying) {
      audio.pause();
    } else {
      audio.play();
    }
    setIsPlaying(!isPlaying);
  }, [isPlaying]);

  // Seek on waveform click/drag
  const handleSeek = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current;
      const audio = audioRef.current;
      if (!canvas || !audio || !duration) return;

      const rect = canvas.getBoundingClientRect();
      const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
      const pct = x / rect.width;
      const newTime = pct * duration;
      audio.currentTime = newTime;
      setCurrentTime(newTime);
      onTimeUpdate?.(newTime);
    },
    [duration, onTimeUpdate],
  );

  const handleMouseDown = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      setIsSeeking(true);
      handleSeek(e);
    },
    [handleSeek],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      if (!isSeeking) return;
      handleSeek(e);
    },
    [isSeeking, handleSeek],
  );

  const handleMouseUp = useCallback(() => {
    setIsSeeking(false);
  }, []);

  // Playback rate
  const RATES = [0.5, 0.75, 1, 1.25, 1.5, 2];
  const cycleRate = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const idx = RATES.indexOf(playbackRate);
    const next = RATES[(idx + 1) % RATES.length];
    audio.playbackRate = next;
    setPlaybackRate(next);
  }, [playbackRate]);

  // Volume
  const toggleMute = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const newMuted = !isMuted;
    audio.muted = newMuted;
    setIsMuted(newMuted);
  }, [isMuted]);

  const handleVolumeChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const audio = audioRef.current;
    if (!audio) return;
    const v = parseFloat(e.target.value);
    audio.volume = v;
    setVolume(v);
    if (v > 0 && isMuted) {
      audio.muted = false;
      setIsMuted(false);
    }
  }, [isMuted]);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.code === 'Space') {
        e.preventDefault();
        togglePlay();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [togglePlay]);

  return (
    <div className="flex flex-col w-full h-full">
      {/* Hidden audio element */}
      <audio
        ref={audioRef}
        src={src}
        preload="metadata"
        onLoadedMetadata={(e) => {
          const d = (e.target as HTMLAudioElement).duration;
          if (d && isFinite(d)) setDuration(d);
        }}
        onError={(e) => {
          const el = e.target as HTMLAudioElement;
          console.error('Audio playback failed:', {
            src,
            code: el.error?.code,
            message: el.error?.message,
          });
        }}
        onEnded={() => setIsPlaying(false)}
        onTimeUpdate={(e) => {
          const t = (e.target as HTMLAudioElement).currentTime;
          if (!isPlaying) {
            setCurrentTime(t);
            onTimeUpdate?.(t);
          }
        }}
      />

      {/* Waveform area */}
      <div
        ref={containerRef}
        className="flex-1 relative cursor-pointer select-none min-h-[120px]"
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        {isDecoding ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="flex flex-col items-center gap-3">
              <div
                className="w-8 h-8 border-2 border-zinc-300 border-t-transparent rounded-full animate-spin"
                style={theme ? { borderColor: theme.accent, borderTopColor: 'transparent' } : undefined}
              />
              <span className="text-xs text-zinc-500">Decoding audio...</span>
            </div>
          </div>
        ) : (
          <canvas
            ref={canvasRef}
            className="absolute inset-0 w-full h-full"
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
          />
        )}

        {/* Chorus marker — thin amber line, clickable to seek there */}
        {!isDecoding && chorusStartSec !== undefined && duration > 0 && chorusStartSec <= duration && (
          <button
            type="button"
            title="Chorus"
            onClick={() => {
              const audio = audioRef.current;
              if (!audio) return;
              audio.currentTime = chorusStartSec;
              setCurrentTime(chorusStartSec);
              onTimeUpdate?.(chorusStartSec);
            }}
            className="absolute top-0 bottom-0 z-10 w-0.5 bg-amber-400/80 hover:bg-amber-300 cursor-pointer"
            style={{ left: `${(chorusStartSec / duration) * 100}%` }}
          >
            <span className="absolute -top-0.5 left-1/2 -translate-x-1/2 w-1.5 h-1.5 rounded-full bg-amber-400" />
          </button>
        )}
      </div>

      {/* Bottom control bar */}
      <div className="flex items-center gap-4 px-6 py-3 border-t border-zinc-800/50 bg-zinc-900/50 shrink-0">
        {/* Play/Pause */}
        <button
          onClick={togglePlay}
          disabled={isDecoding}
          className="p-2 rounded-full transition-opacity hover:opacity-90 disabled:opacity-40"
          style={{
            backgroundColor: theme?.accent ?? NEUTRAL_PLAYED,
            color: theme?.onAccent ?? '#0a0a0a',
          }}
        >
          {isPlaying ? <Pause size={18} /> : <Play size={18} className="ml-0.5" />}
        </button>

        {/* Time */}
        <span className="text-sm text-zinc-300 tabular-nums font-medium min-w-[80px]">
          {formatTime(currentTime)} / {formatTime(duration)}
        </span>

        {/* Filename */}
        <span className="text-xs text-zinc-500 truncate flex-1 min-w-0">
          {filename}
        </span>

        {/* Playback rate */}
        <button
          onClick={cycleRate}
          className="px-2 py-0.5 text-xs font-medium text-zinc-400 hover:text-zinc-200 bg-zinc-800 hover:bg-zinc-700 rounded transition-colors tabular-nums min-w-[40px]"
        >
          {playbackRate === 1 ? '1x' : `${playbackRate}x`}
        </button>

        {/* Volume */}
        <div className="flex items-center gap-2">
          <button
            onClick={toggleMute}
            className="text-zinc-400 hover:text-zinc-200 transition-colors"
          >
            {isMuted || volume === 0 ? <VolumeX size={16} /> : <Volume2 size={16} />}
          </button>
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={isMuted ? 0 : volume}
            onChange={handleVolumeChange}
            style={{
              accentColor: theme?.accent ?? NEUTRAL_PLAYED,
              ['--sw' as string]: theme?.accent ?? NEUTRAL_PLAYED,
            } as React.CSSProperties}
            className="w-20 h-1 bg-zinc-700 rounded-full appearance-none cursor-pointer
              [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3
              [&::-webkit-slider-thumb]:bg-[var(--sw)] [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:cursor-pointer"
          />
        </div>
      </div>
    </div>
  );
};
