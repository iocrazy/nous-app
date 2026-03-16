import { useState, useEffect, useRef, useCallback } from 'react';
import { StoryboardFrame } from '../../types';

// ─── Types ────────────────────────────────────────────────────────────────────

export type PlaybackSpeed = 0.25 | 0.5 | 1 | 1.5 | 2;

interface AnimaticPlayerState {
  isPlaying: boolean;
  currentFrameIndex: number;
  currentTime: number;
  playbackSpeed: PlaybackSpeed;
}

interface AnimaticPlayerActions {
  play: () => void;
  pause: () => void;
  stop: () => void;
  nextFrame: () => void;
  prevFrame: () => void;
  setSpeed: (speed: PlaybackSpeed) => void;
  seekToFrame: (index: number) => void;
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useAnimaticPlayer(frames: StoryboardFrame[]): AnimaticPlayerState & AnimaticPlayerActions {
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentFrameIndex, setCurrentFrameIndex] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [playbackSpeed, setPlaybackSpeed] = useState<PlaybackSpeed>(1);

  const rafRef = useRef<number | null>(null);
  const lastTimestampRef = useRef<number | null>(null);
  const frameElapsedRef = useRef<number>(0);

  const totalFrames = frames.length;

  // Compute elapsed time for current frame, advance when duration exceeded
  const tick = useCallback(
    (timestamp: number) => {
      if (!lastTimestampRef.current) {
        lastTimestampRef.current = timestamp;
      }

      const delta = (timestamp - lastTimestampRef.current) / 1000; // seconds
      lastTimestampRef.current = timestamp;

      frameElapsedRef.current += delta * playbackSpeed;

      setCurrentTime((prev) => prev + delta * playbackSpeed);

      setCurrentFrameIndex((prevIndex) => {
        const frame = frames[prevIndex];
        if (!frame) return prevIndex;

        const frameDuration = frame.duration_seconds;

        if (frameElapsedRef.current >= frameDuration) {
          frameElapsedRef.current = frameElapsedRef.current - frameDuration;
          const nextIndex = prevIndex + 1;

          if (nextIndex >= totalFrames) {
            // Reached end — stop playback
            setIsPlaying(false);
            return 0;
          }

          return nextIndex;
        }

        return prevIndex;
      });

      rafRef.current = requestAnimationFrame(tick);
    },
    [frames, playbackSpeed, totalFrames]
  );

  // Start / stop RAF loop based on isPlaying
  useEffect(() => {
    if (!isPlaying) {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      lastTimestampRef.current = null;
      return;
    }

    rafRef.current = requestAnimationFrame(tick);

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [isPlaying, tick]);

  // ─── Actions ──────────────────────────────────────────────────────────────

  const play = useCallback(() => {
    if (totalFrames === 0) return;
    frameElapsedRef.current = 0;
    lastTimestampRef.current = null;
    setIsPlaying(true);
  }, [totalFrames]);

  const pause = useCallback(() => {
    setIsPlaying(false);
  }, []);

  const stop = useCallback(() => {
    setIsPlaying(false);
    setCurrentFrameIndex(0);
    setCurrentTime(0);
    frameElapsedRef.current = 0;
    lastTimestampRef.current = null;
  }, []);

  const nextFrame = useCallback(() => {
    setCurrentFrameIndex((prev) => Math.min(prev + 1, totalFrames - 1));
    frameElapsedRef.current = 0;
  }, [totalFrames]);

  const prevFrame = useCallback(() => {
    setCurrentFrameIndex((prev) => Math.max(prev - 1, 0));
    frameElapsedRef.current = 0;
  }, []);

  const setSpeed = useCallback((speed: PlaybackSpeed) => {
    setPlaybackSpeed(speed);
  }, []);

  const seekToFrame = useCallback((index: number) => {
    const clamped = Math.max(0, Math.min(index, totalFrames - 1));
    setCurrentFrameIndex(clamped);
    frameElapsedRef.current = 0;
    lastTimestampRef.current = null;
  }, [totalFrames]);

  return {
    isPlaying,
    currentFrameIndex,
    currentTime,
    playbackSpeed,
    play,
    pause,
    stop,
    nextFrame,
    prevFrame,
    setSpeed,
    seekToFrame,
  };
}
