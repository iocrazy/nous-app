import { useState, useCallback, useRef, useEffect, type PointerEvent as ReactPointerEvent } from 'react';
import { Crop, Check, X } from 'lucide-react';
import { UiButton } from '../../../components/ui';
import { cropImageSource } from '../application/toolProcessor';
import { loadImageElement } from '../application/imageData';

interface CropToolEditorProps {
  imageUrl: string;
  onConfirm: (resultUrl: string) => void;
  onCancel: () => void;
}

const ASPECT_RATIOS = [
  { label: 'Free', value: 'free' },
  { label: '1:1', value: '1:1' },
  { label: '16:9', value: '16:9' },
  { label: '9:16', value: '9:16' },
  { label: '4:3', value: '4:3' },
  { label: '3:4', value: '3:4' },
] as const;

type HandlePosition = 'nw' | 'ne' | 'sw' | 'se' | 'n' | 's' | 'e' | 'w' | 'move';

const HANDLE_SIZE_PX = 10;
const MIN_CROP_PERCENT = 0.05;

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

export function CropToolEditor({ imageUrl, onConfirm, onCancel }: CropToolEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [aspectRatio, setAspectRatio] = useState('free');
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [imageDimensions, setImageDimensions] = useState({ width: 0, height: 0 });
  const [cropRect, setCropRect] = useState({ x: 0.1, y: 0.1, width: 0.8, height: 0.8 });
  const dragRef = useRef<{
    handle: HandlePosition;
    startX: number;
    startY: number;
    startRect: typeof cropRect;
    containerWidth: number;
    containerHeight: number;
  } | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const img = await loadImageElement(imageUrl);
        setImageDimensions({ width: img.naturalWidth, height: img.naturalHeight });
      } catch { setError('Failed to load image'); }
    })();
  }, [imageUrl]);

  useEffect(() => {
    if (aspectRatio === 'free') return;
    const [w, h] = aspectRatio.split(':').map(Number);
    const targetRatio = w / h;
    const imgRatio = imageDimensions.width / imageDimensions.height;
    if (imgRatio > targetRatio) {
      const cropW = (targetRatio / imgRatio) * 0.8;
      setCropRect({ x: (1 - cropW) / 2, y: 0.1, width: cropW, height: 0.8 });
    } else {
      const cropH = (imgRatio / targetRatio) * 0.8;
      setCropRect({ x: 0.1, y: (1 - cropH) / 2, width: 0.8, height: cropH });
    }
  }, [aspectRatio, imageDimensions]);

  const handlePointerDown = useCallback(
    (handle: HandlePosition, event: ReactPointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      event.stopPropagation();
      const container = containerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      dragRef.current = {
        handle,
        startX: event.clientX,
        startY: event.clientY,
        startRect: { ...cropRect },
        containerWidth: rect.width,
        containerHeight: rect.height,
      };
      (event.target as HTMLElement).setPointerCapture(event.pointerId);
    },
    [cropRect]
  );

  const handlePointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (!drag) return;
      const dx = (event.clientX - drag.startX) / drag.containerWidth;
      const dy = (event.clientY - drag.startY) / drag.containerHeight;
      const sr = drag.startRect;

      let next = { ...sr };
      const h = drag.handle;

      if (h === 'move') {
        next = {
          ...sr,
          x: clamp(sr.x + dx, 0, 1 - sr.width),
          y: clamp(sr.y + dy, 0, 1 - sr.height),
        };
      } else {
        if (h.includes('w')) {
          const newX = clamp(sr.x + dx, 0, sr.x + sr.width - MIN_CROP_PERCENT);
          next.width = sr.width + (sr.x - newX);
          next.x = newX;
        }
        if (h.includes('e')) {
          next.width = clamp(sr.width + dx, MIN_CROP_PERCENT, 1 - sr.x);
        }
        if (h.includes('n')) {
          const newY = clamp(sr.y + dy, 0, sr.y + sr.height - MIN_CROP_PERCENT);
          next.height = sr.height + (sr.y - newY);
          next.y = newY;
        }
        if (h.includes('s')) {
          next.height = clamp(sr.height + dy, MIN_CROP_PERCENT, 1 - sr.y);
        }

        // Enforce aspect ratio lock
        if (aspectRatio !== 'free') {
          const [w, h2] = aspectRatio.split(':').map(Number);
          const targetRatio = w / h2;
          const imgRatio = imageDimensions.width / imageDimensions.height;
          const adjustedRatio = targetRatio / imgRatio;
          if (drag.handle.length === 2) {
            // Corner drag — adjust height to match width
            next.height = clamp(next.width / adjustedRatio, MIN_CROP_PERCENT, 1 - next.y);
          }
        }
      }

      setCropRect(next);
    },
    [aspectRatio, imageDimensions]
  );

  const handlePointerUp = useCallback(() => { dragRef.current = null; }, []);

  const handleConfirm = useCallback(async () => {
    setProcessing(true);
    setError(null);
    try {
      const result = await cropImageSource(imageUrl, {
        x: Math.round(cropRect.x * imageDimensions.width),
        y: Math.round(cropRect.y * imageDimensions.height),
        width: Math.round(cropRect.width * imageDimensions.width),
        height: Math.round(cropRect.height * imageDimensions.height),
      });
      onConfirm(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Crop failed');
    } finally {
      setProcessing(false);
    }
  }, [imageUrl, cropRect, imageDimensions, onConfirm]);

  const handlePositions: Array<{ pos: HandlePosition; style: React.CSSProperties; cursor: string }> = [
    { pos: 'nw', style: { left: `calc(${cropRect.x * 100}% - ${HANDLE_SIZE_PX / 2}px)`, top: `calc(${cropRect.y * 100}% - ${HANDLE_SIZE_PX / 2}px)` }, cursor: 'nwse-resize' },
    { pos: 'ne', style: { left: `calc(${(cropRect.x + cropRect.width) * 100}% - ${HANDLE_SIZE_PX / 2}px)`, top: `calc(${cropRect.y * 100}% - ${HANDLE_SIZE_PX / 2}px)` }, cursor: 'nesw-resize' },
    { pos: 'sw', style: { left: `calc(${cropRect.x * 100}% - ${HANDLE_SIZE_PX / 2}px)`, top: `calc(${(cropRect.y + cropRect.height) * 100}% - ${HANDLE_SIZE_PX / 2}px)` }, cursor: 'nesw-resize' },
    { pos: 'se', style: { left: `calc(${(cropRect.x + cropRect.width) * 100}% - ${HANDLE_SIZE_PX / 2}px)`, top: `calc(${(cropRect.y + cropRect.height) * 100}% - ${HANDLE_SIZE_PX / 2}px)` }, cursor: 'nwse-resize' },
    { pos: 'n', style: { left: `calc(${(cropRect.x + cropRect.width / 2) * 100}% - ${HANDLE_SIZE_PX / 2}px)`, top: `calc(${cropRect.y * 100}% - ${HANDLE_SIZE_PX / 2}px)` }, cursor: 'ns-resize' },
    { pos: 's', style: { left: `calc(${(cropRect.x + cropRect.width / 2) * 100}% - ${HANDLE_SIZE_PX / 2}px)`, top: `calc(${(cropRect.y + cropRect.height) * 100}% - ${HANDLE_SIZE_PX / 2}px)` }, cursor: 'ns-resize' },
    { pos: 'w', style: { left: `calc(${cropRect.x * 100}% - ${HANDLE_SIZE_PX / 2}px)`, top: `calc(${(cropRect.y + cropRect.height / 2) * 100}% - ${HANDLE_SIZE_PX / 2}px)` }, cursor: 'ew-resize' },
    { pos: 'e', style: { left: `calc(${(cropRect.x + cropRect.width) * 100}% - ${HANDLE_SIZE_PX / 2}px)`, top: `calc(${(cropRect.y + cropRect.height / 2) * 100}% - ${HANDLE_SIZE_PX / 2}px)` }, cursor: 'ew-resize' },
  ];

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex items-center gap-2 text-sm font-medium text-text-dark">
        <Crop className="h-4 w-4" />
        <span>Crop Image</span>
      </div>

      <div
        ref={containerRef}
        className="relative overflow-hidden rounded-lg border border-[rgba(255,255,255,0.1)] bg-bg-dark/60 select-none"
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      >
        <img src={imageUrl} alt="Crop preview" className="block max-h-[300px] w-full object-contain" draggable={false} />

        {/* Dimmed overlay outside crop area */}
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute inset-0 bg-black/50" />
          <div
            className="absolute bg-transparent"
            style={{
              left: `${cropRect.x * 100}%`,
              top: `${cropRect.y * 100}%`,
              width: `${cropRect.width * 100}%`,
              height: `${cropRect.height * 100}%`,
              boxShadow: '0 0 0 9999px rgba(0,0,0,0.5)',
            }}
          />
        </div>

        {/* Crop area border */}
        <div
          className="absolute border-2 border-indigo-400/80 cursor-move"
          style={{
            left: `${cropRect.x * 100}%`,
            top: `${cropRect.y * 100}%`,
            width: `${cropRect.width * 100}%`,
            height: `${cropRect.height * 100}%`,
          }}
          onPointerDown={(e) => handlePointerDown('move', e)}
        />

        {/* Rule of thirds grid inside crop */}
        <div className="absolute pointer-events-none" style={{
          left: `${cropRect.x * 100}%`, top: `${cropRect.y * 100}%`,
          width: `${cropRect.width * 100}%`, height: `${cropRect.height * 100}%`,
        }}>
          <div className="absolute left-1/3 top-0 bottom-0 w-px bg-white/20" />
          <div className="absolute left-2/3 top-0 bottom-0 w-px bg-white/20" />
          <div className="absolute top-1/3 left-0 right-0 h-px bg-white/20" />
          <div className="absolute top-2/3 left-0 right-0 h-px bg-white/20" />
        </div>

        {/* Drag handles */}
        {handlePositions.map(({ pos, style, cursor }) => (
          <div
            key={pos}
            className="absolute z-10 rounded-sm border border-white bg-indigo-500"
            style={{ ...style, width: HANDLE_SIZE_PX, height: HANDLE_SIZE_PX, cursor }}
            onPointerDown={(e) => handlePointerDown(pos, e)}
          />
        ))}
      </div>

      <div className="flex flex-wrap gap-1.5">
        {ASPECT_RATIOS.map((ar) => (
          <button key={ar.value} type="button" onClick={() => setAspectRatio(ar.value)}
            className={`rounded-full px-2.5 py-1 text-[11px] transition-colors ${
              aspectRatio === ar.value
                ? 'bg-indigo-600 text-white'
                : 'bg-[rgba(255,255,255,0.08)] text-text-muted hover:bg-[rgba(255,255,255,0.14)]'
            }`}
          >{ar.label}</button>
        ))}
      </div>

      <div className="text-[10px] text-text-muted">
        {Math.round(cropRect.width * imageDimensions.width)} x {Math.round(cropRect.height * imageDimensions.height)} px
      </div>

      {error && <div className="text-xs text-red-400">{error}</div>}

      <div className="flex justify-end gap-2">
        <UiButton size="sm" variant="ghost" onClick={onCancel}>
          <X className="h-3.5 w-3.5" /> Cancel
        </UiButton>
        <UiButton size="sm" variant="primary" disabled={processing} onClick={handleConfirm}>
          <Check className="h-3.5 w-3.5" /> {processing ? 'Processing...' : 'Apply Crop'}
        </UiButton>
      </div>
    </div>
  );
}
