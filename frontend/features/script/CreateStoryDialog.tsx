import { useState, useCallback } from 'react';
import { X, Sparkles } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { generateOutline } from '../../services/scriptService';

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

export function CreateStoryDialog({ isOpen, onClose }: Props) {
  const { scriptId } = useParams<{ scriptId: string }>();

  const [premise, setPremise] = useState('');
  const [chapterCount, setChapterCount] = useState(5);
  const [generating, setGenerating] = useState(false);

  const handleGenerate = useCallback(async () => {
    if (!premise.trim() || !scriptId) return;
    setGenerating(true);

    try {
      await generateOutline({
        script_id: scriptId,
        premise: premise.trim(),
        chapter_count: chapterCount,
      });
      // Async task dispatched — user sees progress in TaskManager
      onClose();
    } catch (err) {
      console.error('[CreateStoryDialog] Failed to dispatch outline generation:', err);
    } finally {
      setGenerating(false);
    }
  }, [premise, chapterCount, scriptId, onClose]);

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
