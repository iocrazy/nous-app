import { useState, useCallback } from 'react';
import { Type, Check, X } from 'lucide-react';
import { UiButton } from '../../../components/ui';
import { annotateImageSource } from '../application/toolProcessor';

interface AnnotateToolEditorProps {
  imageUrl: string;
  onConfirm: (resultUrl: string) => void;
  onCancel: () => void;
}

const POSITIONS = [
  { label: 'Top', value: 'top' as const },
  { label: 'Center', value: 'center' as const },
  { label: 'Bottom', value: 'bottom' as const },
];

export function AnnotateToolEditor({ imageUrl, onConfirm, onCancel }: AnnotateToolEditorProps) {
  const [text, setText] = useState('');
  const [position, setPosition] = useState<'top' | 'center' | 'bottom'>('bottom');
  const [color, setColor] = useState('#FFFFFF');
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = useCallback(async () => {
    if (!text.trim()) {
      setError('Please enter annotation text');
      return;
    }
    setProcessing(true);
    setError(null);
    try {
      const result = await annotateImageSource(imageUrl, text.trim(), { position, color });
      onConfirm(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Annotation failed');
    } finally {
      setProcessing(false);
    }
  }, [imageUrl, text, position, color, onConfirm]);

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex items-center gap-2 text-sm font-medium text-text-dark">
        <Type className="h-4 w-4" />
        <span>Annotate Image</span>
      </div>

      <div className="overflow-hidden rounded-lg border border-[rgba(255,255,255,0.1)] bg-bg-dark/60">
        <img src={imageUrl} alt="Annotate preview" className="block max-h-[240px] w-full object-contain" />
      </div>

      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Enter annotation text..."
        autoFocus
        className="rounded border border-[rgba(255,255,255,0.14)] bg-bg-dark/60 px-3 py-2 text-sm text-text-dark placeholder:text-text-muted/60 focus:border-blue-500 focus:outline-none"
        onKeyDown={(e) => {
          if (e.key === 'Enter' && text.trim()) void handleConfirm();
          if (e.key === 'Escape') onCancel();
        }}
      />

      <div className="flex items-center gap-3">
        <div className="flex gap-1.5">
          {POSITIONS.map((pos) => (
            <button
              key={pos.value}
              type="button"
              onClick={() => setPosition(pos.value)}
              className={`rounded-full px-2.5 py-1 text-[11px] transition-colors ${
                position === pos.value
                  ? 'bg-blue-600 text-white'
                  : 'bg-[rgba(255,255,255,0.08)] text-text-muted hover:bg-[rgba(255,255,255,0.14)]'
              }`}
            >
              {pos.label}
            </button>
          ))}
        </div>

        <label className="ml-auto flex items-center gap-1.5 text-xs text-text-muted">
          Color
          <input
            type="color"
            value={color}
            onChange={(e) => setColor(e.target.value)}
            className="h-5 w-5 cursor-pointer rounded border-none bg-transparent"
          />
        </label>
      </div>

      {error && <div className="text-xs text-red-400">{error}</div>}

      <div className="flex justify-end gap-2">
        <UiButton size="sm" variant="ghost" onClick={onCancel}>
          <X className="h-3.5 w-3.5" />
          Cancel
        </UiButton>
        <UiButton size="sm" variant="primary" disabled={processing || !text.trim()} onClick={handleConfirm}>
          <Check className="h-3.5 w-3.5" />
          {processing ? 'Processing...' : 'Apply'}
        </UiButton>
      </div>
    </div>
  );
}
