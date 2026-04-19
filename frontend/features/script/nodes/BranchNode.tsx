import { memo, useState, useCallback } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { FilePlus } from 'lucide-react';
import { type ChapterNodeData, useScriptCanvasStore, type ScriptNode } from '../../../stores/scriptCanvasStore';
import { ScriptTipTapEditor } from '../components/ScriptTipTapEditor';

export const BranchNode = memo(({ id, data, selected }: NodeProps<ScriptNode>) => {
  const updateNodeData = useScriptCanvasStore((s) => s.updateNodeData);
  const editingNodeId = useScriptCanvasStore((s) => s.editingNodeId);
  const setEditingNodeId = useScriptCanvasStore((s) => s.setEditingNodeId);
  const [titleEditing, setTitleEditing] = useState(false);
  const [titleInput, setTitleInput] = useState(data.title);

  const handleTitleBlur = useCallback(() => {
    setTitleEditing(false);
    if (titleInput.trim() !== data.title) {
      updateNodeData(id, { title: titleInput.trim() });
    }
  }, [id, titleInput, data.title, updateNodeData]);

  const handleContentUpdate = useCallback(
    (json: Record<string, unknown>, _html: string) => {
      updateNodeData(id, { contentJson: json });
    },
    [id, updateNodeData],
  );

  const isEditing = editingNodeId === id;

  return (
    <div
      className={[
        'w-[300px] rounded-xl border border-l-[3px] border-l-amber-500 bg-zinc-900 shadow-lg transition-colors',
        selected ? 'border-purple-500 ring-1 ring-purple-500/30' : 'border-zinc-700',
      ].join(' ')}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-3 !h-3 !bg-zinc-500"
      />

      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-800">
        <FilePlus size={13} className="text-amber-400 shrink-0" />
        <span className="text-[10px] font-medium text-amber-400 uppercase tracking-wider">
          Supplement
        </span>
      </div>

      {/* Title Row */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-800">
        <span className="flex items-center justify-center w-6 h-6 rounded-full bg-purple-600 text-[11px] font-bold text-white shrink-0">
          {(data as ChapterNodeData).chapterNumber}
        </span>
        {titleEditing ? (
          <input
            className="flex-1 bg-transparent text-sm font-medium text-white outline-none border-b border-purple-500"
            value={titleInput}
            onChange={(e) => setTitleInput(e.target.value)}
            onBlur={handleTitleBlur}
            onKeyDown={(e) => e.key === 'Enter' && handleTitleBlur()}
            autoFocus
          />
        ) : (
          <button
            className="flex-1 text-left text-sm font-medium text-zinc-100 truncate hover:text-white"
            onDoubleClick={() => setTitleEditing(true)}
          >
            {data.title || 'Untitled Branch'}
          </button>
        )}
      </div>

      {/* Content Area */}
      <div
        className="px-3 py-2 min-h-[80px] cursor-text"
        onClick={() => {
          if (!isEditing) setEditingNodeId(id);
        }}
      >
        {isEditing ? (
          <ScriptTipTapEditor
            contentJson={(data as ChapterNodeData).contentJson ?? null}
            onUpdate={handleContentUpdate}
            placeholder="Start writing..."
            editable
          />
        ) : (
          <p className="text-xs text-zinc-400 leading-relaxed">
            {(data as ChapterNodeData).content?.trim() || (
              <span className="text-zinc-600 italic">Start writing...</span>
            )}
          </p>
        )}
      </div>

      <Handle
        type="source"
        position={Position.Bottom}
        id="source"
        className="!w-3 !h-3 !bg-amber-500"
      />
    </div>
  );
});

BranchNode.displayName = 'BranchNode';
