import { useState, useCallback } from 'react';
import { X, BookOpen, Loader2 } from 'lucide-react';
import { useScriptCanvasStore } from '../../stores/scriptCanvasStore';
import { expandChapter } from '../../services/scriptService';
import { useTaskCompletion } from '../../hooks/useTaskCompletion';
import { useToast } from '../../components/Toast';
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
  const reloadScript = useScriptCanvasStore((s) => s.reloadScript);
  const { addToast } = useToast();

  const [expansionRequest, setExpansionRequest] = useState('');
  const [taskId, setTaskId] = useState<string | null>(null);
  const [expanding, setExpanding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Watch the dispatched workflow; reload the canvas from the server once
  // the chapter content has been written, or surface the failure.
  useTaskCompletion(taskId, {
    onComplete: async () => {
      try {
        if (scriptId) await reloadScript(scriptId);
        addToast('Chapter expanded', 'success');
        handleClose();
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
        setExpanding(false);
        setTaskId(null);
        console.error('[ExpandChapterDialog] Reload failed:', message);
      }
    },
    onError: (task) => {
      const message = task.error_msg || 'Expansion failed';
      setError(message);
      addToast(message, 'error');
      setExpanding(false);
      setTaskId(null);
    },
  });

  const handleExpand = useCallback(async () => {
    if (!scriptId) return;
    setExpanding(true);
    setError(null);

    try {
      const { task_id } = await expandChapter({
        script_id: scriptId,
        chapter_id: chapterId,
        title,
        summary,
        expansion_request: expansionRequest.trim() || undefined,
      });
      setTaskId(task_id);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      setExpanding(false);
      addToast(message, 'error');
      console.error('[ExpandChapterDialog] Dispatch failed:', message);
    }
  }, [scriptId, chapterId, title, summary, expansionRequest, addToast]);

  const handleClose = useCallback(() => {
    setExpansionRequest('');
    setTaskId(null);
    setExpanding(false);
    setError(null);
    onClose();
  }, [onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[560px] max-h-[90vh] flex flex-col bg-zinc-900 rounded-xl border border-zinc-700 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-800 flex-shrink-0">
          <div className="flex items-center gap-2">
            <BookOpen size={16} className="text-indigo-400" />
            <h3 className="text-sm font-semibold text-white">Expand Chapter with AI</h3>
          </div>
          <button onClick={handleClose} className="text-zinc-500 hover:text-zinc-300">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-3 overflow-y-auto flex-1">
          {/* Chapter title */}
          <div>
            <p className="text-xs font-medium text-zinc-400 mb-1">Chapter</p>
            <p className="text-sm text-zinc-200 font-medium">{title}</p>
          </div>

          {/* Summary (read-only) */}
          <div>
            <p className="text-xs font-medium text-zinc-400 mb-1">Summary to expand</p>
            <p className="text-sm text-zinc-300 bg-zinc-800 rounded-lg px-3 py-2">
              {summary || 'No summary provided'}
            </p>
          </div>

          {/* Expansion request input */}
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">
              Expansion request{' '}
              <span className="text-zinc-600 font-normal">(optional)</span>
            </label>
            <textarea
              className="w-full bg-zinc-800 text-sm text-zinc-200 rounded-lg px-3 py-2 resize-none outline-none focus:ring-1 focus:ring-indigo-500/50 min-h-[72px] disabled:opacity-60"
              placeholder="e.g. Add more dialogue, intensify conflict..."
              value={expansionRequest}
              onChange={(e) => setExpansionRequest(e.target.value)}
              maxLength={2000}
              disabled={expanding}
            />
          </div>

          <p className="text-xs text-zinc-500">
            {expanding
              ? 'AI is expanding this chapter. This can take 5–30 seconds…'
              : 'AI will generate 3-5 paragraphs of prose from this summary.'}
          </p>

          {error && (
            <p className="text-xs text-red-400 bg-red-900/20 rounded-lg px-3 py-2">{error}</p>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-zinc-800 flex-shrink-0">
          <button
            onClick={handleClose}
            className="px-3 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 rounded-lg hover:bg-zinc-800 transition-colors"
          >
            Cancel
          </button>

          <button
            onClick={handleExpand}
            disabled={expanding || !summary}
            className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {expanding ? <Loader2 size={12} className="animate-spin" /> : <BookOpen size={12} />}
            {expanding ? 'Expanding...' : 'Expand with AI'}
          </button>
        </div>
      </div>
    </div>
  );
}
