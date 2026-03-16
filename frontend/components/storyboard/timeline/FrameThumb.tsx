import React, { useCallback } from 'react';
import { ArrowRight, Image } from 'lucide-react';
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
  const handleClick = useCallback(() => {
    onSelect(frame.id);
  }, [frame.id, onSelect]);

  const shotAbbr = abbreviateShotType(frame.shot_type);
  const transitionIcon = TRANSITION_ICONS[frame.transition_type] ?? '|';

  return (
    <div className="relative flex items-center flex-shrink-0">
      {/* Thumbnail card */}
      <div
        draggable
        onClick={handleClick}
        onDragStart={(e) => onDragStart(e, frame.id)}
        onDragOver={onDragOver}
        onDrop={(e) => onDrop(e, frame.id)}
        className={[
          'relative w-20 h-14 rounded-lg overflow-hidden cursor-pointer border-2 transition-all select-none',
          selected
            ? 'border-blue-500 shadow-[0_0_0_1px_#3b82f6]'
            : 'border-gray-700 hover:border-gray-500',
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
