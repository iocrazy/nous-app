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
        'relative bg-yellow-950/60 border rounded-xl shadow-lg min-w-[180px] max-w-[300px] transition-all',
        selected ? 'border-yellow-500 shadow-yellow-500/20' : 'border-yellow-800',
      ].join(' ')}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 pt-2.5 pb-1.5 border-b border-yellow-900/50">
        <Type size={13} className="text-yellow-400" />
        <span className="text-xs font-semibold text-yellow-300">Annotation</span>
      </div>

      {/* Text body */}
      <div className="p-2">
        <textarea
          className={[
            'w-full bg-transparent text-sm text-yellow-100 placeholder-yellow-700',
            'resize-none focus:outline-none min-h-[80px]',
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
