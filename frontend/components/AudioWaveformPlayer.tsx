import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Play, Pause, Volume2, VolumeX } from 'lucide-react';
import { buildSodaTheme, type SodaTheme } from '../utils/sodaTheme';

interface AudioWaveformPlayerProps {
  src: string;
  filename: string;
  duration?: number;
  /** Emits the playback position (seconds) on every time update / frame. */
  onTimeUpdate?: (seconds: number) => void;
  /** Chorus / highlight marker position (seconds) overlaid on the waveform. */
  chorusStartSec?: number;
  /** Track's own Soda palette. When absent, a stable per-track color is derived. */
  theme?: SodaTheme;
  /**
   * Layout variant.
   * - `'full'` (default): desktop look — large waveform above a control bar
   *   with play / time / speed / volume. Unchanged from before.
   * - `'compact'`: mobile-audio color-block look — a single row of
   *   play + time + a short waveform that doubles as the seek bar. NO speed
   *   chip (relocated to the stats row) and NO volume (phones use physical
   *   volume keys). Playback rate is controlled externally via the
   *   `playbackRate` + `onPlaybackRateChange` props.
   * - `'capsule'`: island audio-stage pill (mock `.player`) — a single ~36px
   *   row of a tint-green play button + a thin cover-tint waveform (played bars
   *   = `rgb(var(--tint))`, unplayed = faint white) + time + speed + a compact
   *   volume toggle. Reads `--tint` off the stage root. Used by the island
   *   desktop audio capsule only.
   */
  layout?: 'full' | 'compact' | 'capsule';
  /**
   * Compact-only: externally-controlled playback rate. When provided, the
   * player applies it to the audio element so a sibling (the stats-row speed
   * chip) can own the value/cycle. Ignored in `'full'` layout.
   */
  playbackRate?: number;
  /** When true, render Set/Clear chorus controls in the full-layout control bar. */
  chorusEditable?: boolean;
  /** Set (seconds) or clear (null) the chorus marker. */
  onChorusChange?: (sec: number | null) => void;
  /** Labels for the chorus controls (i18n supplied by the caller). */
  setChorusLabel?: string;
  clearChorusLabel?: string;
}

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
  layout = 'full',
  playbackRate: externalPlaybackRate,
  chorusEditable,
  onChorusChange,
  setChorusLabel = 'Set chorus',
  clearChorusLabel = 'Clear',
}) => {
  const isCompact = layout === 'compact';
  const isCapsule = layout === 'capsule';
  // Compact lives in a narrow row, so 200 hair-thin bars compress into an
  // unreadable blur. Fewer, wider bars (and a proportional vertical margin
  // below) make the waveform legible even for short tracks. The capsule row is
  // wider than mobile-compact but still short, so ~90 thin bars (per the mock's
  // 90-bar wave) read cleanly.
  const barCount = isCompact ? 56 : isCapsule ? 90 : BAR_COUNT;
  // Use the track's Soda palette when given; otherwise derive a stable vivid
  // color from the src so every audio player is colorful + consistent (no flat
  // gray) regardless of source (downloads / uploads / share).
  const tm = useMemo(() => theme ?? buildSodaTheme(null, src), [theme, src]);

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
        const blockSize = Math.floor(channel.length / barCount);
        const bars: number[] = [];

        for (let i = 0; i < barCount; i++) {
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
  }, [src, barCount]);

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
    // A fixed 32px margin crushes amplitude on the short compact height (~56px
    // → only 12px per half). Use a proportional margin so bars fill the space.
    const verticalMargin = isCompact ? H * 0.16 : isCapsule ? H * 0.12 : 32;
    const maxBarH = (H - verticalMargin) / 2;
    const barW = W / barCount;
    const gap = Math.max(0.5, barW * 0.18); // gap between bars

    // Colors. Capsule (mock `.wave`): played bars = cover-tint `rgb(var(--tint))`
    // read live off the stage root, unplayed = faint white rgba(255,255,255,.16).
    // Other layouts keep the track palette.
    let playedColor: string;
    let playedDimColor: string;
    let unplayedColor: string;
    let unplayedDimColor: string;
    if (isCapsule) {
      const tintRaw = getComputedStyle(container).getPropertyValue('--tint').trim() || '99,102,241';
      playedColor = `rgb(${tintRaw})`;
      playedDimColor = `rgb(${tintRaw})`;
      unplayedColor = 'rgba(255,255,255,0.16)';
      unplayedDimColor = 'rgba(255,255,255,0.16)';
    } else {
      playedColor = tm.accent;
      playedDimColor = tm.accentSoft;
      unplayedColor = tm.waveUnplayed;
      unplayedDimColor = tm.waveUnplayed; // slightly dim mirror (globalAlpha below)
    }

    for (let i = 0; i < waveform.length; i++) {
      const amp = waveform[i];
      const x = i * barW;
      const barH = Math.max(1, amp * maxBarH);
      const isPlayed = i / barCount < progress;

      // Main color based on played state
      const mainColor = isPlayed ? playedColor : unplayedColor;
      const dimColor = isPlayed ? playedDimColor : unplayedDimColor;

      // Draw upper half
      ctx.fillStyle = mainColor;
      ctx.fillRect(x + gap / 2, centerY - barH, barW - gap, barH);

      // Draw lower half (slightly dimmer mirror). Capsule bars are uniform
      // (no dim mirror) to match the mock's flat `.wave i` color blocks.
      ctx.save();
      ctx.globalAlpha = isCapsule ? 1 : (isPlayed ? 1 : 0.7);
      ctx.fillStyle = dimColor;
      ctx.fillRect(x + gap / 2, centerY, barW - gap, barH * 0.8);
      ctx.restore();
    }

    // Center line + playback cursor — omitted for the capsule (the mock wave is
    // a clean bar row whose progress is shown purely by the tint/faint split).
    if (!isCapsule) {
      ctx.strokeStyle = 'rgba(63, 63, 70, 0.5)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, centerY);
      ctx.lineTo(W, centerY);
      ctx.stroke();

      if (duration > 0) {
        const cursorX = progress * W;
        ctx.strokeStyle = tm.lyricActive;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(cursorX, 8);
        ctx.lineTo(cursorX, H - 8);
        ctx.stroke();
      }
    }
  }, [waveform, currentTime, duration, tm, barCount, isCompact, isCapsule]);

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

  // Compact layout: playback rate is owned by the parent (stats-row speed chip).
  // Apply it to the audio element whenever it changes.
  useEffect(() => {
    if (!isCompact || typeof externalPlaybackRate !== 'number') return;
    const audio = audioRef.current;
    if (audio) audio.playbackRate = externalPlaybackRate;
  }, [isCompact, externalPlaybackRate]);

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

  // Shared <audio> element used by both layouts.
  const audioEl = (
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
  );

  // Compact (mobile-audio color block): play + time + short waveform-as-seek-bar
  // on ONE row. No speed chip (lives in the stats row), no volume.
  if (isCompact) {
    return (
      <div className="flex items-center gap-3.5 w-full">
        {audioEl}
        {/* Play / Pause */}
        <button
          onClick={togglePlay}
          disabled={isDecoding}
          aria-label={isPlaying ? 'Pause' : 'Play'}
          className="flex items-center justify-center w-11 h-11 rounded-full bg-white/[0.18] hover:bg-white/[0.26] active:bg-white/30 transition-colors text-white shrink-0 disabled:opacity-40"
        >
          {isPlaying ? <Pause size={18} /> : <Play size={18} className="ml-0.5" />}
        </button>

        {/* Waveform — takes the wide middle so the bars aren't compressed, and
            acts as the seek bar. Time sits to the right, compact. */}
        <div
          ref={containerRef}
          className="relative flex-1 min-w-0 h-14 cursor-pointer select-none"
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        >
          {isDecoding ? (
            <div className="absolute inset-0 flex items-center">
              <div className="w-full h-1 rounded-full bg-white/15 overflow-hidden">
                <div
                  className="h-full w-1/3 animate-pulse rounded-full"
                  style={{ backgroundColor: tm.accent }}
                />
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
            />
          )}
        </div>

        {/* Time — right side, compact */}
        <span className="text-[12px] text-white/80 tabular-nums whitespace-nowrap shrink-0">
          {formatTime(currentTime)} / {formatTime(duration)}
        </span>
      </div>
    );
  }

  // Capsule (island audio stage, mock `.player`): one ~36px row of
  // tint play button + thin cover-tint waveform + time + speed + volume.
  if (isCapsule) {
    return (
      <div className="flex items-center gap-3 w-full">
        {audioEl}
        {/* Play / Pause — mock `.play`: 38px tint circle, dark glyph, tint glow. */}
        <button
          onClick={togglePlay}
          disabled={isDecoding}
          aria-label={isPlaying ? 'Pause' : 'Play'}
          className="audio-play-btn"
        >
          {isPlaying ? <Pause size={14} fill="currentColor" /> : <Play size={14} className="ml-0.5" fill="currentColor" />}
        </button>

        {/* Waveform — mock `.wave`: flex:1, 36px, doubles as the seek bar. */}
        <div
          ref={containerRef}
          className="relative flex-1 min-w-0 h-9 cursor-pointer select-none"
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        >
          {isDecoding ? (
            <div className="absolute inset-0 flex items-center">
              <div className="w-full h-1 rounded-full bg-white/15 overflow-hidden">
                <div
                  className="h-full w-1/3 animate-pulse rounded-full"
                  style={{ backgroundColor: 'rgb(var(--tint,99,102,241))' }}
                />
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
          {/* Chorus badge — mock `.chorus`: amber pill poking above the wave,
              positioned at the real chorus time; click seeks there. */}
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
              className="audio-chorus-badge"
              style={{ left: `${(chorusStartSec / duration) * 100}%` }}
            >
              CHORUS
            </button>
          )}
        </div>

        {/* Time — mock `.ptime`. */}
        <span className="audio-ptime">
          {formatTime(currentTime)} / {formatTime(duration)}
        </span>

        {/* Side controls — mock `.pside`: speed "1×" + a compact volume toggle
            (kept for parity with the full player; mock omits it). */}
        <div className="audio-pside">
          <button
            onClick={cycleRate}
            className="hover:text-[color:var(--content,#e7e7ea)] transition-colors tabular-nums"
          >
            {playbackRate === 1 ? '1×' : `${playbackRate}×`}
          </button>
          <button
            onClick={toggleMute}
            aria-label={isMuted || volume === 0 ? 'Unmute' : 'Mute'}
            className="hover:text-[color:var(--content,#e7e7ea)] transition-colors"
          >
            {isMuted || volume === 0 ? <VolumeX size={14} /> : <Volume2 size={14} />}
          </button>
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={isMuted ? 0 : volume}
            onChange={handleVolumeChange}
            aria-label="Volume"
            style={{ accentColor: 'rgb(var(--tint,99,102,241))' }}
            className="w-12 h-1 bg-white/15 rounded-full appearance-none cursor-pointer"
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col w-full h-full">
      {/* Hidden audio element */}
      {audioEl}

      {/* Waveform area */}
      <div
        ref={containerRef}
        className="flex-1 relative cursor-pointer select-none min-h-[72px] sm:min-h-[120px]"
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        {isDecoding ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="flex flex-col items-center gap-3">
              <div
                className="w-8 h-8 border-2 border-ink-300 border-t-transparent rounded-full animate-spin"
                style={{ borderColor: tm.accent, borderTopColor: 'transparent' }}
              />
              <span className="text-xs text-ink-500">Decoding audio...</span>
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
      <div className="flex items-center gap-4 px-6 py-3 border-t border-ink-800/50 bg-ink-900/50 shrink-0">
        {/* Play/Pause */}
        <button
          onClick={togglePlay}
          disabled={isDecoding}
          className="p-2 rounded-full transition-opacity hover:opacity-90 disabled:opacity-40"
          style={{
            backgroundColor: tm.accent,
            color: tm.onAccent,
          }}
        >
          {isPlaying ? <Pause size={18} /> : <Play size={18} className="ml-0.5" />}
        </button>

        {/* Time */}
        <span className="text-sm text-ink-300 tabular-nums font-medium min-w-[80px]">
          {formatTime(currentTime)} / {formatTime(duration)}
        </span>

        {/* Spacer — filename intentionally not shown (pushes controls right) */}
        <span className="flex-1 min-w-0" />

        {/* Chorus set/clear — uploaded-audio only (opt-in via chorusEditable) */}
        {chorusEditable &&
          (chorusStartSec === undefined ? (
            <button
              type="button"
              onClick={() => onChorusChange?.(currentTime)}
              className="px-2 py-0.5 text-xs font-medium text-amber-300 hover:text-amber-200 bg-ink-800 hover:bg-ink-700 rounded transition-colors whitespace-nowrap"
            >
              {setChorusLabel}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => onChorusChange?.(null)}
              className="px-2 py-0.5 text-xs font-medium text-amber-300 hover:text-amber-200 bg-ink-800 hover:bg-ink-700 rounded transition-colors whitespace-nowrap"
            >
              {clearChorusLabel}
            </button>
          ))}

        {/* Playback rate */}
        <button
          onClick={cycleRate}
          className="px-2 py-0.5 text-xs font-medium text-ink-400 hover:text-ink-200 bg-ink-800 hover:bg-ink-700 rounded transition-colors tabular-nums min-w-[40px]"
        >
          {playbackRate === 1 ? '1x' : `${playbackRate}x`}
        </button>

        {/* Volume */}
        <div className="flex items-center gap-2">
          <button
            onClick={toggleMute}
            className="text-ink-400 hover:text-ink-200 transition-colors"
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
              accentColor: tm.accent,
              ['--sw' as string]: tm.accent,
            } as React.CSSProperties}
            className="w-20 h-1 bg-ink-700 rounded-full appearance-none cursor-pointer
              [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3
              [&::-webkit-slider-thumb]:bg-[var(--sw)] [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:cursor-pointer"
          />
        </div>
      </div>
    </div>
  );
};
