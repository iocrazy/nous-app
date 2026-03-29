import { useState, useCallback } from 'react';
import { X, GitBranch, Loader2 } from 'lucide-react';
import { useScriptCanvasStore } from '../../stores/scriptCanvasStore';
import { createBranches } from '../../services/scriptService';
import { useParams } from 'react-router-dom';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  chapterId: string;
  title: string;
  summary: string;
}

export function CreateBranchDialog({ isOpen, onClose, chapterId, title, summary }: Props) {
  const { scriptId } = useParams<{ scriptId: string }>();
  const addChapterNode = useScriptCanvasStore((s) => s.addChapterNode);
  const nodes = useScriptCanvasStore((s) => s.nodes);

  const [branchCount, setBranchCount] = useState(2);
  const [branchType, setBranchType] = useState<'choice' | 'condition'>('choice');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCreate = useCallback(async () => {
    if (!scriptId) return;
    setLoading(true);
    setError(null);

    try {
      const result = await createBranches({
        script_id: scriptId,
        chapter_id: chapterId,
        title,
        summary,
        branch_count: branchCount,
        branch_type: branchType,
      });

      const parentNode = nodes.find((n) => n.id === chapterId);
      const parentX = parentNode?.position.x ?? 400;
      const parentY = parentNode?.position.y ?? 100;
      const BRANCH_X_OFFSET = 350;
      const BRANCH_Y_OFFSET = 250;

      for (let i = 0; i < result.branches.length; i++) {
        const branch = result.branches[i];
        const xOffset = (i - result.branches.length / 2 + 0.5) * BRANCH_X_OFFSET;
        addChapterNode(
          { x: parentX + xOffset, y: parentY + BRANCH_Y_OFFSET },
          {
            title: branch.title ?? `Branch ${i + 1}`,
            summary: branch.summary ?? '',
            chapterNumber: nodes.length + i + 1,
            branchLabel: branch.branch_label,
            branchType: branchType,
          },
        );
      }

      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      console.error('[CreateBranchDialog] Failed:', message);
    } finally {
      setLoading(false);
    }
  }, [scriptId, chapterId, title, summary, branchCount, branchType, nodes, addChapterNode, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[480px] bg-zinc-900 rounded-xl border border-zinc-700 shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-800">
          <div className="flex items-center gap-2">
            <GitBranch size={16} className="text-amber-400" />
            <h3 className="text-sm font-semibold text-white">Create Story Branches</h3>
          </div>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300">
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          <div>
            <p className="text-xs font-medium text-zinc-400 mb-1">Branching from</p>
            <p className="text-sm text-zinc-200 font-medium">{title}</p>
            <p className="text-xs text-zinc-400 mt-1 line-clamp-2">{summary}</p>
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">Branch Type</label>
            <div className="flex gap-2">
              {(['choice', 'condition'] as const).map((type) => (
                <button
                  key={type}
                  onClick={() => setBranchType(type)}
                  className={`flex-1 px-3 py-2 text-xs rounded-lg border transition-colors ${
                    branchType === type
                      ? 'border-amber-500 bg-amber-900/30 text-amber-300'
                      : 'border-zinc-700 bg-zinc-800 text-zinc-400 hover:border-zinc-600'
                  }`}
                >
                  {type === 'choice' ? 'Character Choice' : 'Condition/Circumstance'}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">Number of Branches</label>
            <div className="flex items-center gap-3">
              {[2, 3, 4].map((n) => (
                <button
                  key={n}
                  onClick={() => setBranchCount(n)}
                  className={`w-10 h-10 rounded-lg text-sm font-medium transition-colors ${
                    branchCount === n
                      ? 'bg-amber-600 text-white'
                      : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700'
                  }`}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>

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
            onClick={handleCreate}
            disabled={loading || !summary}
            className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium text-white bg-amber-600 hover:bg-amber-500 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? <Loader2 size={12} className="animate-spin" /> : <GitBranch size={12} />}
            {loading ? 'Generating...' : `Create ${branchCount} Branches`}
          </button>
        </div>
      </div>
    </div>
  );
}
