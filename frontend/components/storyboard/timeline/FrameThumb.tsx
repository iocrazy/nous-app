import React, { useCallback, useState } from 'react';
import { Image } from 'lucide-react';
import { StoryboardFrame } from '../../../types';

// ─── Props ────────────────────────────────────────────────────────────────────

interface FrameThumbProps {
  frame: StoryboardFrame;
  index: number;
  selected?: boolean;
  onSelect: (frameId: string) => void;
  onDragStart: (e: React.DragEvent, frameId: string) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent, frameId: string) => void;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const SHOT_ABBREVIATIONS: Record<string, string> = {
  'Extreme Close-up': 'ECU',
  'Close-up': 'CU',
  'Medium Close-up': 'MCU',
  Medium: 'MS',
  'Medium Wide': 'MW',
  Wide: 'WS',
  'Extreme Wide': 'EWS',
};

function abbreviateShotType(shotType?: string): string {
  if (!shotType) return '';
  return SHOT_ABBREVIATIONS[shotType] ?? shotType.slice(0, 2).toUpperCase();
}

const TRANSITION_ICONS: Record<string, string> = {
  cut: '|',
  fade: '~',
  dissolve: '≈',
};

// ─── Component ────────────────────────────────────────────────────────────────

const FrameThumb = React.memo(function FrameThumb({
  frame,
  index,
  selected = false,
  onSelect,
  onDragStart,
  onDragOver,
  onDrop,
}: FrameThumbProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);

  const handleClick = useCallback(() => {
    onSelect(frame.id);
  }, [frame.id, onSelect]);

  const handleDragStart = useCallback(
    (e: React.DragEvent) => {
      setIsDragging(true);
      onDragStart(e, frame.id);
    },
    [frame.id, onDragStart],
  );

  const handleDragEnd = useCallback(() => {
    setIsDragging(false);
  }, []);

  const handleDragOver = useCallback(
    (e: React.DragEvent) => {
      setIsDragOver(true);
      onDragOver(e);
    },
    [onDragOver],
  );

  const handleDragLeave = useCallback(() => {
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      setIsDragOver(false);
      onDrop(e, frame.id);
    },
    [frame.id, onDrop],
  );

  const shotAbbr = abbreviateShotType(frame.shot_type);
  const transitionIcon = TRANSITION_ICONS[frame.transition_type] ?? '|';

  return (
    <div className="relative flex items-center flex-shrink-0">
      {/* Drop indicator line */}
      {isDragOver && (
        <div className="absolute -left-0.5 top-0 bottom-0 w-0.5 bg-blue-400 rounded-full z-10" />
      )}

      {/* Thumbnail card */}
      <div
        draggable
        onClick={handleClick}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={[
          'relative w-20 h-14 rounded-lg overflow-hidden cursor-pointer border-2 transition-all select-none',
          isDragging ? 'opacity-40 border-blue-400' : '',
          isDragOver ? 'border-blue-400 scale-105' : '',
          !isDragging && !isDragOver && selected
            ? 'border-blue-500 shadow-[0_0_0_1px_#3b82f6]'
            : '',
          !isDragging && !isDragOver && !selected
            ? 'border-gray-700 hover:border-gray-500'
            : '',
        ].join(' ')}
      >
        {/* Image or placeholder */}
        {frame.thumbnail_url || frame.image_url ? (
          <img
            src={frame.thumbnail_url ?? frame.image_url}
            alt={`Frame ${index + 1}`}
            className="w-full h-full object-cover"
            draggable={false}
          />
        ) : (
          <div className="w-full h-full bg-gray-800 flex items-center justify-center">
            <Image size={16} className="text-gray-600" />
          </div>
        )}

        {/* Frame number badge */}
        <div className="absolute top-0.5 left-0.5 px-1 py-0.5 bg-black/70 rounded text-[9px] text-gray-300 font-mono leading-none">
          {index + 1}
        </div>

        {/* Shot type badge */}
        {shotAbbr && (
          <div className="absolute top-0.5 right-0.5 px-1 py-0.5 bg-blue-900/80 rounded text-[9px] text-blue-200 font-medium leading-none">
            {shotAbbr}
          </div>
        )}

        {/* Duration */}
        <div className="absolute bottom-0 left-0 right-0 px-1 py-0.5 bg-black/60 text-[9px] text-gray-300 text-center leading-none">
          {frame.duration_seconds.toFixed(1)}s
        </div>
      </div>

      {/* Transition icon between frames */}
      <div className="flex items-center justify-center w-5 text-gray-600 text-xs flex-shrink-0">
        <span title={`Transition: ${frame.transition_type}`}>{transitionIcon}</span>
      </div>
    </div>
  );
});

export default FrameThumb;
