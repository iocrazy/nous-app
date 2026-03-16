import React, { useState, useEffect, useCallback, useRef } from 'react';
import { X, Image } from 'lucide-react';
import { StoryboardFrame } from '../../../types';

// ─── Props ────────────────────────────────────────────────────────────────────

interface AnimaticPlayerProps {
  frames: StoryboardFrame[];
  startIndex?: number;
  onClose: () => void;
}

// ─── Component ────────────────────────────────────────────────────────────────

const AnimaticPlayer = React.memo(function AnimaticPlayer({
  frames,
  startIndex = 0,
  onClose,
}: AnimaticPlayerProps) {
  const [currentIndex, setCurrentIndex] = useState(startIndex);
  const [playing, setPlaying] = useState(true);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const currentFrame = frames[currentIndex];
  const isVideo = Boolean(
    currentFrame?.image_url && currentFrame.image_url.match(/\.(mp4|webm|mov)$/i)
  );

  const advance = useCallback(() => {
    setCurrentIndex((prev) => {
      if (prev >= frames.length - 1) {
        setPlaying(false);
        return prev;
      }
      return prev + 1;
    });
  }, [frames.length]);

  // Auto-advance based on duration_seconds
  useEffect(() => {
    if (!playing || !currentFrame) return;
    if (isVideo) return; // video element handles its own timing

    const duration = (currentFrame.duration_seconds ?? 3) * 1000;
    timerRef.current = setTimeout(advance, duration);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [playing, currentFrame, isVideo, advance]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  if (!currentFrame) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black flex flex-col items-center justify-center">
      {/* Close button */}
      <button
        onClick={onClose}
        className="absolute top-4 right-4 p-2 rounded-full bg-white/10 hover:bg-white/20 text-white transition-colors"
        title="Close (Esc)"
      >
        <X size={20} />
      </button>

      {/* Frame counter */}
      <div className="absolute top-4 left-4 px-3 py-1.5 rounded-full bg-black/60 text-sm text-white font-mono">
        {currentIndex + 1} / {frames.length}
      </div>

      {/* Frame display */}
      <div className="relative w-full h-full flex items-center justify-center p-8">
        {isVideo ? (
          <video
            key={currentFrame.id}
            src={currentFrame.image_url}
            autoPlay
            onEnded={advance}
            className="max-w-full max-h-full rounded-xl shadow-2xl"
          />
        ) : currentFrame.image_url ? (
          <img
            src={currentFrame.image_url}
            alt={`Frame ${currentIndex + 1}`}
            className="max-w-full max-h-full object-contain rounded-xl shadow-2xl"
          />
        ) : (
          <div className="flex flex-col items-center gap-4 text-gray-600">
            <Image size={64} />
            <p className="text-sm">No image for this frame</p>
          </div>
        )}
      </div>

      {/* Progress dots */}
      <div className="absolute bottom-6 flex items-center gap-1.5">
        {frames.map((_, i) => (
          <button
            key={i}
            onClick={() => { setCurrentIndex(i); setPlaying(true); }}
            className={[
              'rounded-full transition-all',
              i === currentIndex
                ? 'w-4 h-2 bg-white'
                : 'w-2 h-2 bg-white/30 hover:bg-white/60',
            ].join(' ')}
          />
        ))}
      </div>
    </div>
  );
});

export default AnimaticPlayer;
