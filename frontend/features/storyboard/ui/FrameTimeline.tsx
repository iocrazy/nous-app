import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Film, Play, Pause, X, Clock } from 'lucide-react';

import { useCanvasStore } from '../../../stores/canvasStore';
import {
  CANVAS_NODE_TYPES,
  type CanvasNode,
  type StoryboardFrameItem,
  type StoryboardSplitNodeData,
} from '../domain/canvasNodes';

// ─── Types ──────────────────────────────────────────────────────────────────

interface FrameTimelineProps {
  onClose: () => void;
  onFrameSelect?: (nodeId: string, frameIndex: number) => void;
}

interface TimelineFrame {
  nodeId: string;
  frameIndex: number;
  frame: StoryboardFrameItem;
  globalIndex: number;
}

type PlaybackSpeed = 1 | 1.5 | 2;

const PLAYBACK_SPEEDS: readonly PlaybackSpeed[] = [1, 1.5, 2] as const;
const DEFAULT_FRAME_DURATION_S = 2;
const FRAME_THUMB_W = 80;
const FRAME_THUMB_H = 60;

// ─── Helpers ────────────────────────────────────────────────────────────────

function isStoryboardNode(node: CanvasNode): boolean {
  return (
    node.type === CANVAS_NODE_TYPES.storyboardSplit ||
    node.type === CANVAS_NODE_TYPES.storyboardGen
  );
}

function collectTimelineFrames(nodes: readonly CanvasNode[]): TimelineFrame[] {
  let globalIndex = 0;
  const result: TimelineFrame[] = [];

  for (const node of nodes) {
    if (!isStoryboardNode(node)) continue;

    const data = node.data as StoryboardSplitNodeData;
    const frames = data.frames ?? [];

    for (let i = 0; i < frames.length; i++) {
      result.push({
        nodeId: node.id,
        frameIndex: i,
        frame: frames[i],
        globalIndex,
      });
      globalIndex++;
    }
  }

  return result;
}

function formatDuration(totalSeconds: number): string {
  const mins = Math.floor(totalSeconds / 60);
  const secs = Math.floor(totalSeconds % 60);
  return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
}

// ─── FrameThumb ─────────────────────────────────────────────────────────────

interface FrameThumbProps {
  entry: TimelineFrame;
  isActive: boolean;
  onClick: (nodeId: string, frameIndex: number) => void;
}

const FrameThumb = memo(function FrameThumb({
  entry,
  isActive,
  onClick,
}: FrameThumbProps) {
  const imageUrl = entry.frame.previewImageUrl ?? entry.frame.imageUrl;

  const handleClick = useCallback(() => {
    onClick(entry.nodeId, entry.frameIndex);
  }, [onClick, entry.nodeId, entry.frameIndex]);

  const borderClass = isActive
    ? 'ring-2 ring-indigo-500 ring-offset-1 ring-offset-zinc-900'
    : 'ring-1 ring-zinc-700 hover:ring-zinc-500';

  return (
    <button
      type="button"
      onClick={handleClick}
      className={`
        relative flex-shrink-0 rounded overflow-hidden cursor-pointer
        transition-all duration-150 ${borderClass}
      `}
      style={{ width: FRAME_THUMB_W, height: FRAME_THUMB_H }}
      title={entry.frame.note || `Frame ${entry.globalIndex + 1}`}
    >
      {imageUrl ? (
        <img
          src={imageUrl}
          alt={`Frame ${entry.globalIndex + 1}`}
          className="w-full h-full object-cover"
          draggable={false}
        />
      ) : (
        <div className="w-full h-full bg-zinc-800 flex items-center justify-center">
          <Film className="w-5 h-5 text-zinc-500" />
        </div>
      )}

      {/* Frame number overlay */}
      <span className="absolute bottom-0 left-0 px-1 text-[10px] leading-4 font-mono text-white bg-black/60 rounded-tr">
        {entry.globalIndex + 1}
      </span>
    </button>
  );
});

// ─── SpeedSelector ──────────────────────────────────────────────────────────

interface SpeedSelectorProps {
  speed: PlaybackSpeed;
  onChange: (speed: PlaybackSpeed) => void;
}

const SpeedSelector = memo(function SpeedSelector({
  speed,
  onChange,
}: SpeedSelectorProps) {
  const handleCycle = useCallback(() => {
    const currentIdx = PLAYBACK_SPEEDS.indexOf(speed);
    const nextIdx = (currentIdx + 1) % PLAYBACK_SPEEDS.length;
    onChange(PLAYBACK_SPEEDS[nextIdx]);
  }, [speed, onChange]);

  return (
    <button
      type="button"
      onClick={handleCycle}
      className="px-2 py-1 text-xs font-medium text-zinc-300 bg-zinc-700 hover:bg-zinc-600 rounded transition-colors"
      title="Playback speed"
    >
      {speed}x
    </button>
  );
});

// ─── FrameTimeline ──────────────────────────────────────────────────────────

export const FrameTimeline = memo(function FrameTimeline({
  onClose,
  onFrameSelect,
}: FrameTimelineProps) {
  const nodes = useCanvasStore((s) => s.nodes);

  const [activeIndex, setActiveIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState<PlaybackSpeed>(1);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);

  const timelineFrames = useMemo(() => collectTimelineFrames(nodes), [nodes]);

  const totalDuration = useMemo(
    () => timelineFrames.length * DEFAULT_FRAME_DURATION_S,
    [timelineFrames.length],
  );

  // Clamp active index when frames change
  useEffect(() => {
    if (activeIndex >= timelineFrames.length && timelineFrames.length > 0) {
      setActiveIndex(timelineFrames.length - 1);
    }
  }, [activeIndex, timelineFrames.length]);

  // Auto-scroll active frame into view
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    const thumb = container.children[activeIndex] as HTMLElement | undefined;
    if (thumb) {
      thumb.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    }
  }, [activeIndex]);

  // Playback timer
  useEffect(() => {
    if (!isPlaying || timelineFrames.length === 0) return;

    const delayMs = (DEFAULT_FRAME_DURATION_S / speed) * 1000;

    timerRef.current = setTimeout(() => {
      setActiveIndex((prev) => {
        const next = prev + 1;
        if (next >= timelineFrames.length) {
          setIsPlaying(false);
          return prev;
        }
        return next;
      });
    }, delayMs);

    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [isPlaying, activeIndex, speed, timelineFrames.length]);

  const handleFrameClick = useCallback(
    (nodeId: string, frameIndex: number) => {
      const entry = timelineFrames.find(
        (f) => f.nodeId === nodeId && f.frameIndex === frameIndex,
      );
      if (entry) {
        setActiveIndex(entry.globalIndex);
      }
      onFrameSelect?.(nodeId, frameIndex);
    },
    [timelineFrames, onFrameSelect],
  );

  const handleTogglePlay = useCallback(() => {
    setIsPlaying((prev) => {
      // If at the end, restart from beginning
      if (!prev && activeIndex >= timelineFrames.length - 1) {
        setActiveIndex(0);
      }
      return !prev;
    });
  }, [activeIndex, timelineFrames.length]);

  const handleSpeedChange = useCallback((newSpeed: PlaybackSpeed) => {
    setSpeed(newSpeed);
  }, []);

  if (timelineFrames.length === 0) {
    return (
      <div className="flex items-center gap-3 px-4 py-3 bg-zinc-900/95 border-t border-zinc-700 backdrop-blur-sm">
        <Film className="w-4 h-4 text-zinc-500" />
        <span className="text-sm text-zinc-400">No frames on canvas</span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={onClose}
          className="p-1 text-zinc-400 hover:text-white transition-colors"
          title="Close timeline"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3 px-4 bg-zinc-900/95 border-t border-zinc-700 backdrop-blur-sm"
      style={{ height: 120 }}
    >
      {/* Stats */}
      <div className="flex flex-col items-start gap-1 flex-shrink-0 min-w-[80px]">
        <span className="text-xs font-medium text-zinc-300">
          {timelineFrames.length} frames
        </span>
        <span className="flex items-center gap-1 text-xs text-zinc-500">
          <Clock className="w-3 h-3" />
          {formatDuration(totalDuration)}
        </span>
      </div>

      {/* Playback controls */}
      <div className="flex items-center gap-2 flex-shrink-0">
        <button
          type="button"
          onClick={handleTogglePlay}
          className="p-1.5 rounded-full bg-indigo-600 hover:bg-indigo-500 text-white transition-colors"
          title={isPlaying ? 'Pause' : 'Play'}
        >
          {isPlaying ? (
            <Pause className="w-4 h-4" />
          ) : (
            <Play className="w-4 h-4" />
          )}
        </button>
        <SpeedSelector speed={speed} onChange={handleSpeedChange} />
      </div>

      {/* Scrollable frame strip */}
      <div
        ref={scrollContainerRef}
        className="flex items-center gap-2 overflow-x-auto flex-1 py-2 scrollbar-thin scrollbar-thumb-zinc-700 scrollbar-track-transparent"
      >
        {timelineFrames.map((entry) => (
          <FrameThumb
            key={`${entry.nodeId}-${entry.frameIndex}`}
            entry={entry}
            isActive={entry.globalIndex === activeIndex}
            onClick={handleFrameClick}
          />
        ))}
      </div>

      {/* Close button */}
      <button
        type="button"
        onClick={onClose}
        className="flex-shrink-0 p-1.5 text-zinc-400 hover:text-white transition-colors rounded hover:bg-zinc-700"
        title="Close timeline"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
});
