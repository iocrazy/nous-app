import { memo, useState, useCallback } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { BookOpen, GitBranch, ChevronDown, ChevronUp, Trash2 } from 'lucide-react';
import { type ChapterNodeData, useScriptCanvasStore, type ScriptNode } from '../../../stores/scriptCanvasStore';

export const ChapterNode = memo(({ id, data, selected }: NodeProps<ScriptNode>) => {
  const updateNodeData = useScriptCanvasStore((s) => s.updateNodeData);
  const deleteNode = useScriptCanvasStore((s) => s.deleteNode);
  const [editing, setEditing] = useState(false);
  const [titleInput, setTitleInput] = useState(data.title);
  const [expanded, setExpanded] = useState(data.isExpanded ?? true);

  const handleTitleBlur = useCallback(() => {
    setEditing(false);
    if (titleInput.trim() !== data.title) {
      updateNodeData(id, { title: titleInput.trim() });
    }
  }, [id, titleInput, data.title, updateNodeData]);

  const handleSummaryChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      updateNodeData(id, { summary: e.target.value });
    },
    [id, updateNodeData],
  );

  return (
    <div
      className={`w-[320px] rounded-xl border bg-zinc-900 shadow-lg transition-colors ${
        selected ? 'border-indigo-500 ring-1 ring-indigo-500/30' : 'border-zinc-700'
      } ${data.branchType ? 'border-l-4 border-l-amber-500' : ''}`}
    >
      <Handle type="target" position={Position.Top} id="target" className="!w-3 !h-3 !bg-zinc-500" />

      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-800">
        <span className="flex items-center justify-center w-6 h-6 rounded-md bg-indigo-600 text-[11px] font-bold text-white">
          {data.chapterNumber}
        </span>
        {editing ? (
          <input
            className="flex-1 bg-transparent text-sm font-semibold text-white outline-none border-b border-indigo-500"
            value={titleInput}
            onChange={(e) => setTitleInput(e.target.value)}
            onBlur={handleTitleBlur}
            onKeyDown={(e) => e.key === 'Enter' && handleTitleBlur()}
            autoFocus
          />
        ) : (
          <button
            className="flex-1 text-left text-sm font-semibold text-zinc-100 truncate hover:text-white"
            onDoubleClick={() => setEditing(true)}
          >
            {data.title || 'Untitled Chapter'}
          </button>
        )}
        {data.branchLabel && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-400 font-medium">
            {data.branchLabel}
          </span>
        )}
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-zinc-500 hover:text-zinc-300 p-0.5"
        >
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      </div>

      {/* Body */}
      {expanded && (
        <div className="px-3 py-2 space-y-2">
          <textarea
            className="w-full bg-zinc-800 text-xs text-zinc-300 rounded-md px-2 py-1.5 resize-none outline-none focus:ring-1 focus:ring-indigo-500/50 min-h-[60px]"
            placeholder="Chapter summary..."
            value={data.summary}
            onChange={handleSummaryChange}
            rows={3}
          />

          <div className="flex items-center gap-1.5">
            <button
              className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-indigo-400 px-1.5 py-0.5 rounded hover:bg-zinc-800 transition-colors"
              title="Expand with AI (P3)"
              disabled
            >
              <BookOpen size={12} />
              Expand
            </button>
            <button
              className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-amber-400 px-1.5 py-0.5 rounded hover:bg-zinc-800 transition-colors"
              title="Create branch (P3)"
              disabled
            >
              <GitBranch size={12} />
              Branch
            </button>
            <div className="flex-1" />
            <button
              onClick={() => deleteNode(id)}
              className="text-zinc-600 hover:text-red-400 p-0.5 rounded hover:bg-zinc-800 transition-colors"
              title="Delete chapter"
            >
              <Trash2 size={12} />
            </button>
          </div>
        </div>
      )}

      <Handle type="source" position={Position.Bottom} id="source" className="!w-3 !h-3 !bg-indigo-500" />
    </div>
  );
});

ChapterNode.displayName = 'ChapterNode';
