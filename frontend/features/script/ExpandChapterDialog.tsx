import { useState, useCallback } from 'react';
import DOMPurify from 'dompurify';
import { X, BookOpen, Loader2, ChevronRight } from 'lucide-react';
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

  const [expansionRequest, setExpansionRequest] = useState('');
  const [resultHtml, setResultHtml] = useState<string | null>(null);
  const [expanding, setExpanding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleExpand = useCallback(async () => {
    if (!scriptId) return;
    setExpanding(true);
    setError(null);

    try {
      const result = await expandChapter({
        script_id: scriptId,
        chapter_id: chapterId,
        title,
        summary,
        expansion_request: expansionRequest.trim() || undefined,
      });

      setResultHtml(result.content);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      console.error('[ExpandChapterDialog] Failed:', message);
    } finally {
      setExpanding(false);
    }
  }, [scriptId, chapterId, title, summary, expansionRequest]);

  const handleConfirm = useCallback(() => {
    if (resultHtml !== null) {
      updateNodeData(chapterId, { content: resultHtml, isExpanded: true });
    }
    onClose();
  }, [resultHtml, chapterId, updateNodeData, onClose]);

  const handleClose = useCallback(() => {
    setResultHtml(null);
    setExpansionRequest('');
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
              className="w-full bg-zinc-800 text-sm text-zinc-200 rounded-lg px-3 py-2 resize-none outline-none focus:ring-1 focus:ring-indigo-500/50 min-h-[72px]"
              placeholder="e.g. Add more dialogue, intensify conflict..."
              value={expansionRequest}
              onChange={(e) => setExpansionRequest(e.target.value)}
              maxLength={2000}
            />
          </div>

          {!resultHtml && (
            <p className="text-xs text-zinc-500">
              AI will generate 3-5 paragraphs of prose from this summary.
            </p>
          )}

          {/* Generated result preview */}
          {resultHtml && (
            <div>
              <div className="flex items-center gap-1.5 mb-2">
                <ChevronRight size={13} className="text-indigo-400" />
                <p className="text-xs font-medium text-zinc-300">Generated Result</p>
              </div>
              <div
                className="bg-zinc-950 rounded-lg px-4 py-3 max-h-[300px] overflow-y-auto text-sm leading-relaxed border border-zinc-800
                  [&_.scene-heading]:text-orange-400 [&_.scene-heading]:font-semibold [&_.scene-heading]:uppercase [&_.scene-heading]:tracking-wide
                  [&_.dialogue]:text-orange-300 [&_.dialogue]:italic
                  [&_p]:text-zinc-300 [&_p]:mb-2"
                dangerouslySetInnerHTML={{
                  __html: DOMPurify.sanitize(resultHtml, {
                    ALLOWED_TAGS: ['h2', 'h3', 'p', 'strong', 'em', 'hr', 'br', 'ul', 'ol', 'li', 'span'],
                  }),
                }}
              />
            </div>
          )}

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

          {resultHtml ? (
            <button
              onClick={handleConfirm}
              className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-500 rounded-lg transition-colors"
            >
              Confirm Replace
            </button>
          ) : (
            <button
              onClick={handleExpand}
              disabled={expanding || !summary}
              className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {expanding ? <Loader2 size={12} className="animate-spin" /> : <BookOpen size={12} />}
              {expanding ? 'Expanding...' : 'Expand with AI'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
