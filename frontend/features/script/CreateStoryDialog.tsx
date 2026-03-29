import { useState, useCallback } from 'react';
import { X, Sparkles } from 'lucide-react';
import { useScriptCanvasStore } from '../../stores/scriptCanvasStore';

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

export function CreateStoryDialog({ isOpen, onClose }: Props) {
  const addChapterNode = useScriptCanvasStore((s) => s.addChapterNode);
  const clearCanvas = useScriptCanvasStore((s) => s.clearCanvas);

  const [premise, setPremise] = useState('');
  const [chapterCount, setChapterCount] = useState(5);
  const [generating, setGenerating] = useState(false);

  const handleGenerate = useCallback(async () => {
    if (!premise.trim()) return;
    setGenerating(true);

    try {
      // P2: Local outline generation (placeholder for AI in P3)
      clearCanvas();

      const chapters = Array.from({ length: chapterCount }, (_, i) => ({
        title: `Chapter ${i + 1}`,
        summary: i === 0
          ? `Opening: ${premise.slice(0, 100)}...`
          : i === chapterCount - 1
          ? 'Conclusion and resolution.'
          : `Development of the story — part ${i + 1}.`,
        chapterNumber: i + 1,
      }));

      const VERTICAL_GAP = 200;
      const START_X = 400;
      const START_Y = 100;

      for (const ch of chapters) {
        addChapterNode(
          { x: START_X, y: START_Y + (ch.chapterNumber - 1) * VERTICAL_GAP },
          ch,
        );
      }

      onClose();
    } finally {
      setGenerating(false);
    }
  }, [premise, chapterCount, addChapterNode, clearCanvas, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[480px] bg-zinc-900 rounded-xl border border-zinc-700 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-800">
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-violet-400" />
            <h3 className="text-sm font-semibold text-white">Generate Story Outline</h3>
          </div>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-4">
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">Story Premise</label>
            <textarea
              className="w-full bg-zinc-800 text-sm text-zinc-200 rounded-lg px-3 py-2 resize-none outline-none focus:ring-1 focus:ring-violet-500/50 min-h-[100px]"
              placeholder="Describe your story idea, setting, main characters, and conflict..."
              value={premise}
              onChange={(e) => setPremise(e.target.value)}
              maxLength={10000}
            />
            <p className="text-[11px] text-zinc-600 mt-1">{premise.length} / 10,000</p>
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">
              Number of Chapters
            </label>
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={2}
                max={20}
                value={chapterCount}
                onChange={(e) => setChapterCount(Number(e.target.value))}
                className="flex-1 accent-violet-500"
              />
              <span className="text-sm font-mono text-zinc-300 w-6 text-center">{chapterCount}</span>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-zinc-800">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 rounded-lg hover:bg-zinc-800 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleGenerate}
            disabled={!premise.trim() || generating}
            className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium text-white bg-violet-600 hover:bg-violet-500 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Sparkles size={12} />
            {generating ? 'Generating...' : 'Generate Outline'}
          </button>
        </div>
      </div>
    </div>
  );
}
