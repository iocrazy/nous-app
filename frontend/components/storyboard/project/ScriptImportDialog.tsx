import React, { useState, useCallback } from 'react';
import { X, FileText, Loader2 } from 'lucide-react';

// ─── Props ────────────────────────────────────────────────────────────────────

interface DetectedScene {
  index: number;
  description: string;
  characters: string[];
}

interface ScriptImportDialogProps {
  onImport: (script: string, styleGuide: string, scenes: DetectedScene[]) => void;
  onCancel: () => void;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function mockDetectScenes(script: string): DetectedScene[] {
  // Placeholder: split by double newlines as "scenes"
  return script
    .split(/\n{2,}/)
    .filter((s) => s.trim().length > 10)
    .slice(0, 8)
    .map((text, i) => ({
      index: i,
      description: text.slice(0, 120).trim(),
      characters: [],
    }));
}

// ─── Component ────────────────────────────────────────────────────────────────

const ScriptImportDialog = React.memo(function ScriptImportDialog({
  onImport,
  onCancel,
}: ScriptImportDialogProps) {
  const [script, setScript] = useState('');
  const [styleGuide, setStyleGuide] = useState('');
  const [scenes, setScenes] = useState<DetectedScene[] | null>(null);
  const [analyzing, setAnalyzing] = useState(false);

  const handleAnalyze = useCallback(async () => {
    if (!script.trim()) return;
    setAnalyzing(true);
    // Simulated async analysis — replace with real AI call
    await new Promise((r) => setTimeout(r, 800));
    setScenes(mockDetectScenes(script));
    setAnalyzing(false);
  }, [script]);

  const handleImport = useCallback(() => {
    if (!scenes) return;
    onImport(script, styleGuide, scenes);
  }, [script, styleGuide, scenes, onImport]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onCancel} />

      <div className="relative bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <div className="flex items-center gap-2">
            <FileText size={16} className="text-blue-400" />
            <h2 className="text-sm font-semibold text-gray-100">Import from Script</h2>
          </div>
          <button onClick={onCancel} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* Script textarea */}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">Script *</label>
            <textarea
              value={script}
              onChange={(e) => { setScript(e.target.value); setScenes(null); }}
              placeholder="Paste your script here..."
              rows={8}
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-xl text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors resize-none font-mono"
            />
          </div>

          {/* Style guide */}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">
              Style Guide{' '}
              <span className="text-gray-600 font-normal">(optional)</span>
            </label>
            <input
              type="text"
              value={styleGuide}
              onChange={(e) => setStyleGuide(e.target.value)}
              placeholder="e.g. Dark film noir, 1940s aesthetic, high contrast..."
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
            />
          </div>

          {/* Analyze button */}
          <button
            onClick={handleAnalyze}
            disabled={!script.trim() || analyzing}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-200 transition-colors disabled:opacity-40"
          >
            {analyzing ? <Loader2 size={14} className="animate-spin" /> : <FileText size={14} />}
            {analyzing ? 'Analyzing...' : 'Detect Scenes'}
          </button>

          {/* Scene preview */}
          {scenes && (
            <div>
              <p className="text-xs font-medium text-gray-400 mb-2">
                Detected {scenes.length} scene{scenes.length !== 1 ? 's' : ''}:
              </p>
              <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
                {scenes.map((scene) => (
                  <div key={scene.index} className="flex gap-2 p-2 bg-gray-800 rounded-lg">
                    <span className="text-xs text-gray-500 font-mono w-5 flex-shrink-0">
                      {scene.index + 1}
                    </span>
                    <p className="text-xs text-gray-300 leading-relaxed">{scene.description}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-gray-700">
          <button onClick={onCancel} className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors">
            Cancel
          </button>
          <button
            onClick={handleImport}
            disabled={!scenes}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Import {scenes ? `(${scenes.length} scenes)` : ''}
          </button>
        </div>
      </div>
    </div>
  );
});

export default ScriptImportDialog;
