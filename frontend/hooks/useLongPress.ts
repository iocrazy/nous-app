import { useRef, useCallback } from 'react';

interface UseLongPressOptions {
  onLongPress: (e: React.TouchEvent) => void;
  onDragStart?: (e: React.TouchEvent) => void;
  threshold?: number;
  moveThreshold?: number;
}

interface UseLongPressReturn {
  onTouchStart: (e: React.TouchEvent) => void;
  onTouchMove: (e: React.TouchEvent) => void;
  onTouchEnd: (e: React.TouchEvent) => void;
  onTouchCancel: (e: React.TouchEvent) => void;
}

export function useLongPress({
  onLongPress,
  onDragStart,
  threshold = 500,
  moveThreshold = 10,
}: UseLongPressOptions): UseLongPressReturn {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startPosRef = useRef<{ x: number; y: number } | null>(null);
  const firedRef = useRef(false);
  const savedEventRef = useRef<React.TouchEvent | null>(null);

  const clear = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    startPosRef.current = null;
    firedRef.current = false;
    savedEventRef.current = null;
  }, []);

  const onTouchStartHandler = useCallback(
    (e: React.TouchEvent) => {
      const touch = e.touches[0];
      startPosRef.current = { x: touch.clientX, y: touch.clientY };
      firedRef.current = false;
      savedEventRef.current = e;

      timerRef.current = setTimeout(() => {
        firedRef.current = true;
        if (savedEventRef.current) {
          onLongPress(savedEventRef.current);
        }
      }, threshold);
    },
    [onLongPress, threshold],
  );

  const onTouchMoveHandler = useCallback(
    (e: React.TouchEvent) => {
      if (!startPosRef.current || firedRef.current) return;

      const touch = e.touches[0];
      const dx = touch.clientX - startPosRef.current.x;
      const dy = touch.clientY - startPosRef.current.y;
      const distance = Math.sqrt(dx * dx + dy * dy);

      if (distance > moveThreshold) {
        if (timerRef.current) {
          clearTimeout(timerRef.current);
          timerRef.current = null;
        }
        firedRef.current = true;
        onDragStart?.(e);
      }
    },
    [onDragStart, moveThreshold],
  );

  const onTouchEndHandler = useCallback(() => {
    clear();
  }, [clear]);

  const onTouchCancelHandler = useCallback(() => {
    clear();
  }, [clear]);

  return {
    onTouchStart: onTouchStartHandler,
    onTouchMove: onTouchMoveHandler,
    onTouchEnd: onTouchEndHandler,
    onTouchCancel: onTouchCancelHandler,
  };
}
