import React, { useCallback } from 'react';
import { NodeProps } from '@xyflow/react';
import { Type } from 'lucide-react';
import { useStoryboardStore } from '../../../stores/storyboardStore';

// ─── TextAnnotationNode ───────────────────────────────────────────────────────
// Standalone note — no connection handles

const TextAnnotationNode = React.memo(function TextAnnotationNode({ id, selected, data }: NodeProps) {
  const { updateNodeData } = useStoryboardStore();

  const nodeData = data as Record<string, unknown>;
  const text = (nodeData.text as string) ?? '';

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      updateNodeData(id, { data_json: { ...nodeData, text: e.target.value } });
    },
    [id, nodeData, updateNodeData]
  );

  return (
    <div
      className={[
        'relative bg-gray-800 border rounded-xl shadow-lg min-w-[180px] max-w-[300px] transition-all',
        selected ? 'border-indigo-500 shadow-indigo-500/20' : 'border-gray-700',
      ].join(' ')}
    >
      {/* Accent bar */}
      <div className="absolute top-0 left-0 right-0 h-0.5 rounded-t-xl bg-amber-500" />

      {/* Header */}
      <div className="flex items-center gap-2 px-3 pt-2.5 pb-1.5 border-b border-gray-700/50">
        <Type size={13} className="text-amber-400" />
        <span className="text-xs font-semibold text-gray-100">Annotation</span>
      </div>

      {/* Text body */}
      <div className="p-2">
        <textarea
          className={[
            'w-full bg-gray-900 border border-gray-600 rounded-lg px-2 py-1.5 text-sm text-gray-100 placeholder-gray-500',
            'resize-none focus:outline-none focus:border-indigo-500 min-h-[80px] transition-colors',
          ].join(' ')}
          placeholder="Type your annotation..."
          value={text}
          onChange={handleChange}
          rows={4}
        />
      </div>
    </div>
  );
});

export default TextAnnotationNode;
