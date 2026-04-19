import { memo, useState, useCallback } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { FileText, GitBranch, Sparkles, Trash2 } from 'lucide-react';
import {
  type ChapterNodeData,
  useScriptCanvasStore,
  type ScriptNode,
} from '../../../stores/scriptCanvasStore';
import { ScriptTipTapEditor } from '../components/ScriptTipTapEditor';

// ---------------------------------------------------------------------------
// Helper: render contentJson as plain-text preview for non-editing state
// ---------------------------------------------------------------------------

function extractTextFromJson(json: Record<string, unknown> | null): string {
  if (!json) return '';
  try {
    const content = json.content as Array<{ type: string; content?: Array<{ text?: string }> }>;
    if (!Array.isArray(content)) return '';
    return content
      .flatMap((node) => node.content ?? [])
      .map((leaf) => leaf.text ?? '')
      .join(' ');
  } catch {
    return '';
  }
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export const ChapterFlowNode = memo(({ id, data, selected }: NodeProps<ScriptNode>) => {
  const nodeData = data as ChapterNodeData;

  const editingNodeId = useScriptCanvasStore((s) => s.editingNodeId);
  const setEditingNodeId = useScriptCanvasStore((s) => s.setEditingNodeId);
  const updateNodeData = useScriptCanvasStore((s) => s.updateNodeData);
  const deleteNode = useScriptCanvasStore((s) => s.deleteNode);
  const openExpandDialog = useScriptCanvasStore((s) => s.openExpandDialog);
  const openBranchDialog = useScriptCanvasStore((s) => s.openBranchDialog);

  const isEditing = editingNodeId === id;

  const [titleInput, setTitleInput] = useState(nodeData.title);
  const [titleEditing, setTitleEditing] = useState(false);

  // -------------------------------------------------------------------------
  // Title handlers
  // -------------------------------------------------------------------------

  const handleTitleBlur = useCallback(() => {
    setTitleEditing(false);
    if (titleInput.trim() !== nodeData.title) {
      updateNodeData(id, { title: titleInput.trim() });
    }
  }, [id, titleInput, nodeData.title, updateNodeData]);

  const handleTitleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      e.stopPropagation();
      if (e.key === 'Enter') handleTitleBlur();
      if (e.key === 'Escape') {
        setTitleInput(nodeData.title);
        setTitleEditing(false);
      }
    },
    [handleTitleBlur, nodeData.title],
  );

  // -------------------------------------------------------------------------
  // TipTap content update handler
  // -------------------------------------------------------------------------

  const handleContentUpdate = useCallback(
    (json: Record<string, unknown>, _html: string) => {
      updateNodeData(id, { contentJson: json });
    },
    [id, updateNodeData],
  );

  // -------------------------------------------------------------------------
  // Event propagation guards for the editing region
  // -------------------------------------------------------------------------

  const stopPropagation = useCallback((e: React.SyntheticEvent) => {
    e.stopPropagation();
  }, []);

  // -------------------------------------------------------------------------
  // Static content preview text
  // -------------------------------------------------------------------------

  const previewText =
    extractTextFromJson(nodeData.contentJson) || nodeData.content || '';

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <div
      draggable={false}
      className={[
        'w-[520px] rounded-xl border bg-zinc-900 shadow-xl transition-colors relative group',
        selected
          ? 'border-purple-500 ring-1 ring-purple-500/30'
          : 'border-zinc-700/50',
        nodeData.branchType ? 'border-l-4 border-l-amber-500' : '',
      ]
        .filter(Boolean)
        .join(' ')}
    >
      {/* ------------------------------------------------------------------ */}
      {/* Delete button — hovers above node, visible on group hover           */}
      {/* ------------------------------------------------------------------ */}
      <button
        onClick={() => deleteNode(id)}
        className="absolute -top-7 right-0 flex items-center gap-1 text-[11px] text-zinc-500 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded hover:bg-zinc-800"
        title="Delete chapter"
      >
        <Trash2 size={12} />
      </button>

      {/* ------------------------------------------------------------------ */}
      {/* Target handle                                                        */}
      {/* ------------------------------------------------------------------ */}
      <Handle
        type="target"
        position={Position.Top}
        id="target"
        className="!w-3 !h-3 !bg-zinc-500"
      />

      {/* ------------------------------------------------------------------ */}
      {/* Chapter label — outside header, top of node                         */}
      {/* ------------------------------------------------------------------ */}
      <div className="flex items-center gap-1.5 px-4 pt-3 pb-0">
        <FileText size={12} className="text-zinc-500 flex-shrink-0" />
        <span className="text-xs text-zinc-500">
          第 {nodeData.chapterNumber} 章 {nodeData.title || 'Untitled'}
        </span>
        {nodeData.branchLabel && (
          <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-400 font-medium">
            {nodeData.branchLabel}
          </span>
        )}
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Header row: purple circle number + editable title                   */}
      {/* ------------------------------------------------------------------ */}
      <div className="flex items-center gap-3 px-4 pt-2 pb-3 border-b border-zinc-800">
        <span className="flex items-center justify-center w-7 h-7 rounded-full bg-purple-600 text-sm font-bold text-white flex-shrink-0">
          {nodeData.chapterNumber}
        </span>

        {titleEditing ? (
          <input
            className="flex-1 bg-transparent text-base font-medium text-white outline-none border-b border-purple-500 py-0.5"
            value={titleInput}
            onChange={(e) => setTitleInput(e.target.value)}
            onBlur={handleTitleBlur}
            onKeyDown={handleTitleKeyDown}
            onMouseDown={stopPropagation}
            autoFocus
          />
        ) : (
          <button
            className="flex-1 text-left text-base font-medium text-white truncate hover:text-zinc-100"
            onDoubleClick={() => setTitleEditing(true)}
          >
            {nodeData.title || 'Untitled Chapter'}
          </button>
        )}
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Content area: TipTap editor or static preview                       */}
      {/* ------------------------------------------------------------------ */}
      <div className="px-4 py-3">
        {isEditing ? (
          <div
            onKeyDown={stopPropagation}
            onMouseDown={stopPropagation}
            onWheel={stopPropagation}
          >
            <ScriptTipTapEditor
              contentJson={nodeData.contentJson}
              onUpdate={handleContentUpdate}
              placeholder="Write chapter content..."
              editable
            />
          </div>
        ) : (
          <div
            role="button"
            tabIndex={0}
            onClick={() => setEditingNodeId(id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') setEditingNodeId(id);
            }}
            className={[
              'min-h-[200px] px-4 py-3 rounded-lg border text-xs text-zinc-400 leading-relaxed cursor-text',
              'transition-colors hover:border-purple-500/50 hover:text-zinc-300',
              'border-zinc-800 bg-zinc-800/40',
            ].join(' ')}
          >
            {previewText ? (
              <p className="line-clamp-5">{previewText}</p>
            ) : (
              <p className="text-zinc-600 italic">Click to start writing...</p>
            )}
          </div>
        )}
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Summary row                                                          */}
      {/* ------------------------------------------------------------------ */}
      {nodeData.summary && (
        <div className="flex items-center gap-2 px-4 pb-2">
          <span className="text-[11px] text-zinc-500 truncate flex-1">
            摘要: {nodeData.summary}
          </span>
          <button
            onClick={() => openExpandDialog(id, nodeData.title, nodeData.summary)}
            className="flex items-center text-amber-500 hover:text-amber-400 transition-colors flex-shrink-0"
            title="Expand from summary"
          >
            <Sparkles size={18} />
          </button>
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Footer: Create Branch button (full width)                           */}
      {/* ------------------------------------------------------------------ */}
      <div className="px-4 pb-4">
        {!nodeData.summary && (
          <button
            onClick={() => openExpandDialog(id, nodeData.title, nodeData.summary)}
            className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-amber-400 mb-2 transition-colors"
            title="Expand from summary"
          >
            <Sparkles size={14} />
            <span>Add summary</span>
          </button>
        )}
        <button
          onClick={() => openBranchDialog(id, nodeData.title, nodeData.summary)}
          className="w-full flex items-center justify-center gap-2 rounded-lg py-2.5 text-sm font-medium text-white bg-gradient-to-r from-purple-600 to-violet-600 hover:from-purple-500 hover:to-violet-500 transition-all shadow-sm"
          title="Create branch"
        >
          <GitBranch size={14} />
          Create Branch
        </button>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Source handle — green connection dot                                */}
      {/* ------------------------------------------------------------------ */}
      <Handle
        type="source"
        position={Position.Bottom}
        id="source"
        className="!w-3 !h-3 !bg-green-500"
      />
    </div>
  );
});

ChapterFlowNode.displayName = 'ChapterFlowNode';
