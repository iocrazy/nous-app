import React, { useState, useCallback, useEffect, useRef } from 'react';
import { X, ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from 'lucide-react';

// ─── Props ────────────────────────────────────────────────────────────────────

interface ImageViewerImage {
  url: string;
  width?: number;
  height?: number;
  size?: number;
}

interface ImageViewerModalProps {
  images: ImageViewerImage[];
  initialIndex?: number;
  onClose: () => void;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatBytes(bytes?: number): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const MIN_ZOOM = 0.25;
const MAX_ZOOM = 5;
const ZOOM_STEP = 0.25;

// ─── Component ────────────────────────────────────────────────────────────────

const ImageViewerModal = React.memo(function ImageViewerModal({
  images,
  initialIndex = 0,
  onClose,
}: ImageViewerModalProps) {
  const [index, setIndex] = useState(
    Math.max(0, Math.min(initialIndex, images.length - 1))
  );
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ mx: 0, my: 0, px: 0, py: 0 });

  const containerRef = useRef<HTMLDivElement>(null);

  const currentImage = images[index];
  const hasPrev = index > 0;
  const hasNext = index < images.length - 1;

  // Reset zoom and pan when image changes
  const goTo = useCallback((newIndex: number) => {
    setIndex(newIndex);
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, []);

  const goNext = useCallback(() => { if (hasNext) goTo(index + 1); }, [hasNext, index, goTo]);
  const goPrev = useCallback(() => { if (hasPrev) goTo(index - 1); }, [hasPrev, index, goTo]);

  // Keyboard navigation
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onClose(); return; }
      if (e.key === 'ArrowRight') goNext();
      if (e.key === 'ArrowLeft') goPrev();
      if (e.key === '+' || e.key === '=') setZoom((z) => Math.min(MAX_ZOOM, z + ZOOM_STEP));
      if (e.key === '-') setZoom((z) => Math.max(MIN_ZOOM, z - ZOOM_STEP));
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose, goNext, goPrev]);

  // Scroll to zoom
  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP;
    setZoom((z) => Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, z + delta)));
  }, []);

  // Drag to pan
  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      setDragging(true);
      setDragStart({ mx: e.clientX, my: e.clientY, px: pan.x, py: pan.y });
    },
    [pan]
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!dragging) return;
      setPan({
        x: dragStart.px + (e.clientX - dragStart.mx),
        y: dragStart.py + (e.clientY - dragStart.my),
      });
    },
    [dragging, dragStart]
  );

  const handleMouseUp = useCallback(() => setDragging(false), []);

  if (!currentImage) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/95 flex flex-col">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-3 bg-black/40">
        <div className="flex items-center gap-2">
          {images.length > 1 && (
            <span className="text-sm text-gray-400 font-mono">
              {index + 1} / {images.length}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setZoom((z) => Math.max(MIN_ZOOM, z - ZOOM_STEP))}
            className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-white/10 transition-colors"
            title="Zoom out (-)"
          >
            <ZoomOut size={16} />
          </button>
          <span className="text-xs text-gray-500 w-12 text-center font-mono">
            {Math.round(zoom * 100)}%
          </span>
          <button
            onClick={() => setZoom((z) => Math.min(MAX_ZOOM, z + ZOOM_STEP))}
            className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-white/10 transition-colors"
            title="Zoom in (+)"
          >
            <ZoomIn size={16} />
          </button>
          <button
            onClick={onClose}
            className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-white/10 transition-colors ml-2"
            title="Close (Esc)"
          >
            <X size={18} />
          </button>
        </div>
      </div>

      {/* Image area */}
      <div
        ref={containerRef}
        className="flex-1 overflow-hidden relative flex items-center justify-center"
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        style={{ cursor: dragging ? 'grabbing' : zoom > 1 ? 'grab' : 'default' }}
      >
        <img
          src={currentImage.url}
          alt={`Image ${index + 1}`}
          draggable={false}
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: 'center',
            maxWidth: '90vw',
            maxHeight: '80vh',
            objectFit: 'contain',
            transition: dragging ? 'none' : 'transform 0.1s ease',
          }}
        />

        {/* Prev / Next arrows */}
        {hasPrev && (
          <button
            onClick={(e) => { e.stopPropagation(); goPrev(); }}
            className="absolute left-4 top-1/2 -translate-y-1/2 p-3 rounded-full bg-black/50 hover:bg-black/70 text-white transition-colors"
            title="Previous"
          >
            <ChevronLeft size={20} />
          </button>
        )}
        {hasNext && (
          <button
            onClick={(e) => { e.stopPropagation(); goNext(); }}
            className="absolute right-4 top-1/2 -translate-y-1/2 p-3 rounded-full bg-black/50 hover:bg-black/70 text-white transition-colors"
            title="Next"
          >
            <ChevronRight size={20} />
          </button>
        )}
      </div>

      {/* Bottom info bar */}
      {(currentImage.width || currentImage.height || currentImage.size) && (
        <div className="flex items-center justify-center gap-4 px-4 py-2 bg-black/40 text-xs text-gray-500">
          {currentImage.width && currentImage.height && (
            <span>{currentImage.width} × {currentImage.height}px</span>
          )}
          {currentImage.size && <span>{formatBytes(currentImage.size)}</span>}
        </div>
      )}
    </div>
  );
});

export default ImageViewerModal;
