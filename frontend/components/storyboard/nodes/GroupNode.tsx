import React, { useCallback, useState } from 'react';
import { NodeProps, NodeResizer } from '@xyflow/react';
import { Square, Edit2, Check } from 'lucide-react';
import { useStoryboardStore } from '../../../stores/storyboardStore';

// ─── GroupNode ────────────────────────────────────────────────────────────────

const DEFAULT_WIDTH = 400;
const DEFAULT_HEIGHT = 300;

const GroupNode = React.memo(function GroupNode({ id, selected, data, width, height }: NodeProps) {
  const { updateNodeData } = useStoryboardStore();
  const [editingTitle, setEditingTitle] = useState(false);

  const nodeData = data as Record<string, unknown>;
  const title = (nodeData.title as string) ?? 'Group';
  const color = (nodeData.color as string) ?? '#374151';

  const w = width ?? DEFAULT_WIDTH;
  const h = height ?? DEFAULT_HEIGHT;

  const handleTitleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      updateNodeData(id, { data_json: { ...nodeData, title: e.target.value } });
    },
    [id, nodeData, updateNodeData]
  );

  const handleResize = useCallback(
    (_: unknown, params: { width: number; height: number }) => {
      updateNodeData(id, { width: params.width, height: params.height });
    },
    [id, updateNodeData]
  );

  return (
    <div
      className={[
        'relative rounded-xl border-2 border-dashed transition-all',
        selected ? 'border-blue-400' : 'border-gray-600',
      ].join(' ')}
      style={{
        width: w,
        height: h,
        backgroundColor: `${color}20`,
      }}
    >
      <NodeResizer
        isVisible={selected}
        minWidth={200}
        minHeight={150}
        onResize={handleResize}
        lineStyle={{ border: '1px solid #3b82f6' }}
        handleStyle={{ width: 8, height: 8, backgroundColor: '#3b82f6', borderRadius: 2 }}
      />

      {/* Title bar */}
      <div className="flex items-center gap-2 absolute top-0 left-0 right-0 px-3 py-2 bg-gray-900/80 rounded-t-xl border-b border-gray-700/50">
        <Square size={12} className="text-gray-400" />
        {editingTitle ? (
          <input
            autoFocus
            className="flex-1 bg-transparent text-sm text-gray-100 focus:outline-none"
            value={title}
            onChange={handleTitleChange}
            onBlur={() => setEditingTitle(false)}
            onKeyDown={(e) => e.key === 'Enter' && setEditingTitle(false)}
          />
        ) : (
          <span className="flex-1 text-sm font-medium text-gray-200 truncate">{title}</span>
        )}
        <button
          onClick={() => setEditingTitle((v) => !v)}
          className="text-gray-500 hover:text-gray-300 transition-colors"
        >
          {editingTitle ? <Check size={12} /> : <Edit2 size={12} />}
        </button>
      </div>
    </div>
  );
});

export default GroupNode;
