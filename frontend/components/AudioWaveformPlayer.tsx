import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Play, Pause, Volume2, VolumeX } from 'lucide-react';

interface AudioWaveformPlayerProps {
  src: string;
  filename: string;
  duration?: number;
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

    // Colors
    const playedColor = 'rgba(129, 140, 248, 1)';      // indigo-400
    const playedDimColor = 'rgba(99, 102, 241, 0.6)';   // indigo-500 dim
    const unplayedColor = 'rgba(113, 113, 122, 0.5)';   // zinc-500
    const unplayedDimColor = 'rgba(82, 82, 91, 0.35)';  // zinc-600

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
      ctx.fillStyle = dimColor;
      ctx.fillRect(x + gap / 2, centerY, barW - gap, barH * 0.8);
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
      ctx.strokeStyle = 'rgba(199, 210, 254, 0.8)'; // indigo-200
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(cursorX, 8);
      ctx.lineTo(cursorX, H - 8);
      ctx.stroke();
    }
  }, [waveform, currentTime, duration]);

  // Animation loop for smooth updates
  useEffect(() => {
    const tick = () => {
      if (audioRef.current) {
        setCurrentTime(audioRef.current.currentTime);
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
    },
    [duration],
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
        onLoadedMetadata={(e) => {
          const d = (e.target as HTMLAudioElement).duration;
          if (d && isFinite(d)) setDuration(d);
        }}
        onEnded={() => setIsPlaying(false)}
        onTimeUpdate={(e) => {
          if (!isPlaying) setCurrentTime((e.target as HTMLAudioElement).currentTime);
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
              <div className="w-8 h-8 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
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
      </div>

      {/* Bottom control bar */}
      <div className="flex items-center gap-4 px-6 py-3 border-t border-zinc-800/50 bg-zinc-900/50 shrink-0">
        {/* Play/Pause */}
        <button
          onClick={togglePlay}
          disabled={isDecoding}
          className="p-2 rounded-full bg-indigo-600 hover:bg-indigo-500 text-white transition-colors disabled:opacity-40"
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
            className="w-20 h-1 bg-zinc-700 rounded-full appearance-none cursor-pointer
              [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3
              [&::-webkit-slider-thumb]:bg-indigo-400 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:cursor-pointer"
          />
        </div>
      </div>
    </div>
  );
};
