import { useState, useCallback, useRef, useEffect } from 'react';
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

export function CropToolEditor({ imageUrl, onConfirm, onCancel }: CropToolEditorProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [aspectRatio, setAspectRatio] = useState('free');
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [imageDimensions, setImageDimensions] = useState({ width: 0, height: 0 });

  // Crop rect as percentage of image (0-1)
  const [cropRect, setCropRect] = useState({ x: 0.1, y: 0.1, width: 0.8, height: 0.8 });

  useEffect(() => {
    void (async () => {
      try {
        const img = await loadImageElement(imageUrl);
        setImageDimensions({ width: img.naturalWidth, height: img.naturalHeight });
      } catch {
        setError('Failed to load image');
      }
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

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex items-center gap-2 text-sm font-medium text-text-dark">
        <Crop className="h-4 w-4" />
        <span>Crop Image</span>
      </div>

      <div className="relative overflow-hidden rounded-lg border border-[rgba(255,255,255,0.1)] bg-bg-dark/60">
        <img
          src={imageUrl}
          alt="Crop preview"
          className="block max-h-[300px] w-full object-contain"
        />
        {/* Crop overlay visualization */}
        <div
          className="absolute border-2 border-indigo-400/80 bg-indigo-400/10"
          style={{
            left: `${cropRect.x * 100}%`,
            top: `${cropRect.y * 100}%`,
            width: `${cropRect.width * 100}%`,
            height: `${cropRect.height * 100}%`,
          }}
        />
      </div>

      <div className="flex flex-wrap gap-1.5">
        {ASPECT_RATIOS.map((ar) => (
          <button
            key={ar.value}
            type="button"
            onClick={() => setAspectRatio(ar.value)}
            className={`rounded-full px-2.5 py-1 text-[11px] transition-colors ${
              aspectRatio === ar.value
                ? 'bg-indigo-600 text-white'
                : 'bg-[rgba(255,255,255,0.08)] text-text-muted hover:bg-[rgba(255,255,255,0.14)]'
            }`}
          >
            {ar.label}
          </button>
        ))}
      </div>

      {error && <div className="text-xs text-red-400">{error}</div>}

      <div className="flex justify-end gap-2">
        <UiButton size="sm" variant="ghost" onClick={onCancel}>
          <X className="h-3.5 w-3.5" />
          Cancel
        </UiButton>
        <UiButton size="sm" variant="primary" disabled={processing} onClick={handleConfirm}>
          <Check className="h-3.5 w-3.5" />
          {processing ? 'Processing...' : 'Apply Crop'}
        </UiButton>
      </div>
    </div>
  );
}
