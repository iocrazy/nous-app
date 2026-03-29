import { useState, useCallback } from 'react';
import { X, BookOpen, Loader2 } from 'lucide-react';
import { useScriptCanvasStore } from '../../stores/scriptCanvasStore';
import { expandChapter } from '../../services/scriptService';
import { useParams } from 'react-router-dom';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  chapterId: string;
  title: string;
  summary: string;
}

export function ExpandChapterDialog({ isOpen, onClose, chapterId, title, summary }: Props) {
  const { scriptId } = useParams<{ scriptId: string }>();
  const updateNodeData = useScriptCanvasStore((s) => s.updateNodeData);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleExpand = useCallback(async () => {
    if (!scriptId) return;
    setLoading(true);
    setError(null);

    try {
      const result = await expandChapter({
        script_id: scriptId,
        chapter_id: chapterId,
        title,
        summary,
      });

      updateNodeData(chapterId, { content: result.content, isExpanded: true });
      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      console.error('[ExpandChapterDialog] Failed:', message);
    } finally {
      setLoading(false);
    }
  }, [scriptId, chapterId, title, summary, updateNodeData, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[480px] bg-zinc-900 rounded-xl border border-zinc-700 shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-800">
          <div className="flex items-center gap-2">
            <BookOpen size={16} className="text-indigo-400" />
            <h3 className="text-sm font-semibold text-white">Expand Chapter with AI</h3>
          </div>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300">
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-3">
          <div>
            <p className="text-xs font-medium text-zinc-400 mb-1">Chapter</p>
            <p className="text-sm text-zinc-200 font-medium">{title}</p>
          </div>
          <div>
            <p className="text-xs font-medium text-zinc-400 mb-1">Summary to expand</p>
            <p className="text-sm text-zinc-300 bg-zinc-800 rounded-lg px-3 py-2">{summary || 'No summary provided'}</p>
          </div>
          <p className="text-xs text-zinc-500">
            AI will generate 3-5 paragraphs of prose from this summary.
          </p>
          {error && (
            <p className="text-xs text-red-400 bg-red-900/20 rounded-lg px-3 py-2">{error}</p>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-3 border-t border-zinc-800">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 rounded-lg hover:bg-zinc-800 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleExpand}
            disabled={loading || !summary}
            className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? <Loader2 size={12} className="animate-spin" /> : <BookOpen size={12} />}
            {loading ? 'Expanding...' : 'Expand with AI'}
          </button>
        </div>
      </div>
    </div>
  );
}
