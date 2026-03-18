import React, { useState, useCallback } from 'react';
import { X, Loader2, AlertCircle } from 'lucide-react';
import { splitImage, SplitImageResult } from '../../../services/storyboardService';

// ─── Props ────────────────────────────────────────────────────────────────────

interface SplitToolProps {
  imageUrl: string;
  projectId: string;
  assetId: string;
  nodeId?: string;
  onComplete: (result: SplitImageResult) => void;
  onCancel: () => void;
}

// ─── Component ────────────────────────────────────────────────────────────────

const SplitTool = React.memo(function SplitTool({
  imageUrl,
  projectId,
  assetId,
  nodeId,
  onComplete,
  onCancel,
}: SplitToolProps) {
  const [rows, setRows] = useState(2);
  const [cols, setCols] = useState(2);
  const [splitting, setSplitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleApply = useCallback(async () => {
    if (!projectId || !assetId) {
      setError('Missing project or asset information.');
      return;
    }

    setSplitting(true);
    setError(null);

    try {
      const result = await splitImage(projectId, assetId, rows, cols, nodeId);
      onComplete(result);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Split failed';
      setError(message);
      console.error('[SplitTool] split failed:', err);
    } finally {
      setSplitting(false);
    }
  }, [projectId, assetId, rows, cols, nodeId, onComplete]);

  const clamp = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70" onClick={onCancel} />

      <div className="relative bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-lg flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <h2 className="text-sm font-semibold text-gray-100">Split Image into Frames</h2>
          <button
            onClick={onCancel}
            disabled={splitting}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors disabled:opacity-50"
          >
            <X size={16} />
          </button>
        </div>

        {/* Controls */}
        <div className="flex items-center gap-6 px-5 py-4 border-b border-gray-800">
          <div className="flex items-center gap-2">
            <label className="text-xs text-gray-400 w-10">Rows</label>
            <input
              type="number"
              min={1}
              max={10}
              value={rows}
              onChange={(e) => setRows(clamp(Number(e.target.value), 1, 10))}
              disabled={splitting}
              className="w-16 px-2 py-1 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-100 text-center focus:outline-none focus:border-blue-500 disabled:opacity-50"
            />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-gray-400 w-10">Cols</label>
            <input
              type="number"
              min={1}
              max={10}
              value={cols}
              onChange={(e) => setCols(clamp(Number(e.target.value), 1, 10))}
              disabled={splitting}
              className="w-16 px-2 py-1 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-100 text-center focus:outline-none focus:border-blue-500 disabled:opacity-50"
            />
          </div>
          <p className="text-xs text-gray-500 ml-auto">
            &rarr; {rows * cols} frames
          </p>
        </div>

        {/* Image preview with grid overlay */}
        <div className="relative m-5 rounded-xl overflow-hidden bg-gray-950" style={{ height: 300 }}>
          <img
            src={imageUrl}
            alt="Split preview"
            className="w-full h-full object-contain"
            draggable={false}
          />

          {/* Grid overlay */}
          <svg
            className="absolute inset-0 w-full h-full pointer-events-none"
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
          >
            {/* Vertical lines */}
            {Array.from({ length: cols - 1 }, (_, i) => (
              <line
                key={`v-${i}`}
                x1={((i + 1) / cols) * 100}
                y1={0}
                x2={((i + 1) / cols) * 100}
                y2={100}
                stroke="rgba(59,130,246,0.8)"
                strokeWidth="0.5"
              />
            ))}
            {/* Horizontal lines */}
            {Array.from({ length: rows - 1 }, (_, i) => (
              <line
                key={`h-${i}`}
                x1={0}
                y1={((i + 1) / rows) * 100}
                x2={100}
                y2={((i + 1) / rows) * 100}
                stroke="rgba(59,130,246,0.8)"
                strokeWidth="0.5"
              />
            ))}
          </svg>
        </div>

        {/* Error */}
        {error && (
          <div className="flex items-start gap-2 mx-5 mb-3 text-xs text-red-400 bg-red-950/30 rounded-lg px-3 py-2">
            <AlertCircle size={14} className="shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-gray-700">
          <button
            onClick={onCancel}
            disabled={splitting}
            className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleApply}
            disabled={splitting}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            {splitting ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                Splitting...
              </>
            ) : (
              `Split into ${rows * cols} Frames`
            )}
          </button>
        </div>
      </div>
    </div>
  );
});

export default SplitTool;
