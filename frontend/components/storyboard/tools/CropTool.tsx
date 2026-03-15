import React, { useState, useCallback, useRef } from 'react';
import { X, Lock, Unlock } from 'lucide-react';

// ─── Props ────────────────────────────────────────────────────────────────────

interface CropRegion {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface CropToolProps {
  imageUrl: string;
  onApply: (region: CropRegion) => void;
  onCancel: () => void;
}

type AspectOption = 'free' | '16:9' | '4:3' | '1:1' | '9:16';

const ASPECT_OPTIONS: { label: string; value: AspectOption }[] = [
  { label: 'Free', value: 'free' },
  { label: '16:9', value: '16:9' },
  { label: '4:3', value: '4:3' },
  { label: '1:1', value: '1:1' },
  { label: '9:16', value: '9:16' },
];

const ASPECT_RATIOS: Record<AspectOption, number | null> = {
  free: null,
  '16:9': 16 / 9,
  '4:3': 4 / 3,
  '1:1': 1,
  '9:16': 9 / 16,
};

// ─── Component ────────────────────────────────────────────────────────────────

const CropTool = React.memo(function CropTool({
  imageUrl,
  onApply,
  onCancel,
}: CropToolProps) {
  const [aspect, setAspect] = useState<AspectOption>('free');
  const [region, setRegion] = useState<CropRegion>({ x: 10, y: 10, width: 80, height: 80 });
  const [dragging, setDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ mx: 0, my: 0, rx: 0, ry: 0 });

  const containerRef = useRef<HTMLDivElement>(null);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      setDragging(true);
      setDragStart({
        mx: e.clientX,
        my: e.clientY,
        rx: region.x,
        ry: region.y,
      });
    },
    [region.x, region.y]
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!dragging) return;
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const dx = ((e.clientX - dragStart.mx) / rect.width) * 100;
      const dy = ((e.clientY - dragStart.my) / rect.height) * 100;
      setRegion((prev) => ({
        ...prev,
        x: Math.max(0, Math.min(100 - prev.width, dragStart.rx + dx)),
        y: Math.max(0, Math.min(100 - prev.height, dragStart.ry + dy)),
      }));
    },
    [dragging, dragStart]
  );

  const handleMouseUp = useCallback(() => setDragging(false), []);

  const handleAspectChange = useCallback((opt: AspectOption) => {
    setAspect(opt);
    const ratio = ASPECT_RATIOS[opt];
    if (ratio !== null) {
      setRegion((prev) => ({
        ...prev,
        height: prev.width / ratio,
      }));
    }
  }, []);

  const handleApply = useCallback(() => {
    onApply(region);
  }, [region, onApply]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70" onClick={onCancel} />

      <div className="relative bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-2xl flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <h2 className="text-sm font-semibold text-gray-100">Crop Image</h2>
          <button onClick={onCancel} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Aspect ratio options */}
        <div className="flex items-center gap-2 px-5 py-3 border-b border-gray-800">
          <span className="text-xs text-gray-500 mr-1">Aspect:</span>
          {ASPECT_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => handleAspectChange(opt.value)}
              className={[
                'flex items-center gap-1 px-2 py-1 rounded-lg text-xs transition-colors',
                aspect === opt.value
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:text-gray-200',
              ].join(' ')}
            >
              {opt.value === 'free' ? <Unlock size={10} /> : <Lock size={10} />}
              {opt.label}
            </button>
          ))}
        </div>

        {/* Image canvas */}
        <div
          ref={containerRef}
          className="relative m-5 rounded-xl overflow-hidden cursor-move select-none"
          style={{ height: 360 }}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        >
          <img src={imageUrl} alt="Crop" className="w-full h-full object-contain bg-gray-950" draggable={false} />

          {/* Dark overlay outside crop region */}
          <div className="absolute inset-0 pointer-events-none">
            {/* top */}
            <div className="absolute top-0 left-0 right-0 bg-black/50" style={{ height: `${region.y}%` }} />
            {/* bottom */}
            <div className="absolute bottom-0 left-0 right-0 bg-black/50" style={{ height: `${100 - region.y - region.height}%` }} />
            {/* left */}
            <div
              className="absolute bg-black/50"
              style={{ top: `${region.y}%`, left: 0, width: `${region.x}%`, height: `${region.height}%` }}
            />
            {/* right */}
            <div
              className="absolute bg-black/50"
              style={{
                top: `${region.y}%`,
                right: 0,
                width: `${100 - region.x - region.width}%`,
                height: `${region.height}%`,
              }}
            />
          </div>

          {/* Crop handle (drag area) */}
          <div
            className="absolute border-2 border-white/80 cursor-move"
            style={{
              left: `${region.x}%`,
              top: `${region.y}%`,
              width: `${region.width}%`,
              height: `${region.height}%`,
            }}
            onMouseDown={handleMouseDown}
          />
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-gray-700">
          <button onClick={onCancel} className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors">
            Cancel
          </button>
          <button
            onClick={handleApply}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors"
          >
            Apply
          </button>
        </div>
      </div>
    </div>
  );
});

export default CropTool;
