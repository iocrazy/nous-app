import React, { useState, useCallback, useRef, useEffect } from 'react';
import {
  Play,
  Pause,
  Square,
  ChevronUp,
  ChevronDown,
  Film,
} from 'lucide-react';
import { StoryboardFrame } from '../../../types';
import FrameThumb from './FrameThumb';
import AnimaticPlayer from './AnimaticPlayer';

// ─── Props ────────────────────────────────────────────────────────────────────

interface FrameTimelineProps {
  frames: StoryboardFrame[];
  selectedFrameId?: string | null;
  onSelectFrame: (frameId: string) => void;
  onReorderFrames: (reordered: StoryboardFrame[]) => void;
}

type PlaybackSpeed = 0.5 | 1 | 1.5 | 2;

const SPEED_OPTIONS: PlaybackSpeed[] = [0.5, 1, 1.5, 2];

// ─── Helpers ──────────────────────────────────────────────────────────────────

function totalDuration(frames: StoryboardFrame[]): number {
  return frames.reduce((sum, f) => sum + (f.duration_seconds ?? 0), 0);
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
    .toString()
    .padStart(2, '0');
  const s = Math.floor(seconds % 60)
    .toString()
    .padStart(2, '0');
  return `${m}:${s}`;
}

// ─── Component ────────────────────────────────────────────────────────────────

const FrameTimeline = React.memo(function FrameTimeline({
  frames,
  selectedFrameId,
  onSelectFrame,
  onReorderFrames,
}: FrameTimelineProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<PlaybackSpeed>(1);
  const [currentTime, setCurrentTime] = useState(0);
  const [showAnimatic, setShowAnimatic] = useState(false);

  const dragIdRef = useRef<string | null>(null);
  const playIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Clean up interval on unmount
  useEffect(() => {
    return () => {
      if (playIntervalRef.current) clearInterval(playIntervalRef.current);
    };
  }, []);

  const total = totalDuration(frames);

  // Play / Pause logic (advances currentTime by interval ticks)
  const handlePlayPause = useCallback(() => {
    if (playing) {
      setPlaying(false);
      if (playIntervalRef.current) clearInterval(playIntervalRef.current);
      return;
    }
    setPlaying(true);
    const tickMs = 100;
    playIntervalRef.current = setInterval(() => {
      setCurrentTime((prev) => {
        const next = prev + (tickMs / 1000) * speed;
        if (next >= total) {
          setPlaying(false);
          if (playIntervalRef.current) clearInterval(playIntervalRef.current);
          return total;
        }
        return next;
      });
    }, tickMs);
  }, [playing, speed, total]);

  const handleStop = useCallback(() => {
    setPlaying(false);
    setCurrentTime(0);
    if (playIntervalRef.current) clearInterval(playIntervalRef.current);
  }, []);

  // Drag-and-drop reorder
  const handleDragStart = useCallback((_e: React.DragEvent, frameId: string) => {
    dragIdRef.current = frameId;
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
  }, []);

  const handleDrop = useCallback(
    (_e: React.DragEvent, targetId: string) => {
      const dragId = dragIdRef.current;
      if (!dragId || dragId === targetId) return;

      const fromIdx = frames.findIndex((f) => f.id === dragId);
      const toIdx = frames.findIndex((f) => f.id === targetId);
      if (fromIdx === -1 || toIdx === -1) return;

      const reordered = [...frames];
      const [moved] = reordered.splice(fromIdx, 1);
      reordered.splice(toIdx, 0, moved);
      onReorderFrames(reordered.map((f, i) => ({ ...f, sort_order: i })));
      dragIdRef.current = null;
    },
    [frames, onReorderFrames]
  );

  const progressPct = total > 0 ? (currentTime / total) * 100 : 0;

  return (
    <>
      <div className="flex-shrink-0 bg-gray-900 border-t border-gray-700 relative">
        {/* Collapse toggle */}
        <button
          onClick={() => setCollapsed((v) => !v)}
          className="absolute -top-5 left-1/2 -translate-x-1/2 flex items-center gap-1 px-2.5 py-0.5 bg-gray-800 border border-gray-700 border-b-0 rounded-t-md text-[10px] text-gray-400 hover:text-gray-200 transition-colors z-10"
        >
          {collapsed ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          Timeline
          {frames.length > 0 && (
            <span className="ml-1 text-gray-600">({frames.length})</span>
          )}
        </button>

        {!collapsed && (
          <>
            {/* Controls row + thumbnail strip combined for compact layout */}
            <div className="flex items-center gap-2 px-3 py-1.5">
              {/* Play/Pause */}
              <button
                onClick={handlePlayPause}
                disabled={frames.length === 0}
                className="p-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 hover:text-white transition-colors disabled:opacity-40"
              >
                {playing ? <Pause size={13} /> : <Play size={13} />}
              </button>

              {/* Stop */}
              <button
                onClick={handleStop}
                disabled={frames.length === 0}
                className="p-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 hover:text-white transition-colors disabled:opacity-40"
              >
                <Square size={13} />
              </button>

              {/* Speed selector */}
              <div className="flex items-center gap-0.5">
                {SPEED_OPTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => setSpeed(s)}
                    className={[
                      'px-1.5 py-0.5 rounded text-[11px] font-mono transition-colors',
                      speed === s
                        ? 'bg-indigo-600 text-white'
                        : 'bg-gray-800 text-gray-400 hover:text-gray-200',
                    ].join(' ')}
                  >
                    {s}x
                  </button>
                ))}
              </div>

              {/* Time display */}
              <span className="text-[11px] font-mono text-gray-400 ml-auto">
                {formatTime(currentTime)} / {formatTime(total)}
              </span>

              {/* Animatic player button */}
              <button
                onClick={() => setShowAnimatic(true)}
                disabled={frames.length === 0}
                title="Open Animatic Player"
                className="flex items-center gap-1 px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 text-[11px] text-gray-400 hover:text-gray-200 transition-colors disabled:opacity-40"
              >
                <Film size={11} /> Preview
              </button>
            </div>

            {/* Thumbnail strip */}
            <div className="overflow-x-auto px-3 pb-1.5 max-h-[80px]">
              {frames.length === 0 ? (
                <p className="text-[11px] text-gray-600 text-center py-1">
                  No frames yet — generate or upload frames to see them here
                </p>
              ) : (
                <div className="flex items-center min-w-max gap-0.5">
                  {frames.map((frame, i) => (
                    <FrameThumb
                      key={frame.id}
                      frame={frame}
                      index={i}
                      selected={selectedFrameId === frame.id}
                      onSelect={onSelectFrame}
                      onDragStart={handleDragStart}
                      onDragOver={handleDragOver}
                      onDrop={handleDrop}
                    />
                  ))}
                </div>
              )}
            </div>

            {/* Progress bar */}
            <div className="h-0.5 bg-gray-800 mx-3 mb-1 rounded-full overflow-hidden">
              <div
                className="h-full bg-indigo-500 transition-all"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          </>
        )}
      </div>

      {/* Animatic player overlay */}
      {showAnimatic && frames.length > 0 && (
        <AnimaticPlayer
          frames={frames}
          onClose={() => setShowAnimatic(false)}
        />
      )}
    </>
  );
});

export default FrameTimeline;
