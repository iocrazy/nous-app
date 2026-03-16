import React, { useCallback } from 'react';
import { NodeProps, Position } from '@xyflow/react';
import { Scissors, Grid3X3 } from 'lucide-react';
import NodeWrapper from './shared/NodeWrapper';
import NodeImagePreview from './shared/NodeImagePreview';
import { nodeControlStyles as s } from './shared/NodeControlStyles';
import { useStoryboardStore } from '../../../stores/storyboardStore';

// ─── Types ────────────────────────────────────────────────────────────────────

interface SplitFrame {
  index: number;
  thumbnailUrl?: string;
}

// ─── StoryboardSplitNode ──────────────────────────────────────────────────────

const StoryboardSplitNode = React.memo(function StoryboardSplitNode({ id, selected, data }: NodeProps) {
  const { updateNodeData } = useStoryboardStore();

  const nodeData = data as Record<string, unknown>;
  const rows = (nodeData.rows as number) ?? 2;
  const cols = (nodeData.cols as number) ?? 3;
  const frames = (nodeData.frames as SplitFrame[]) ?? [];
  const sourceImageUrl = nodeData.sourceImageUrl as string | undefined;
  const locked = nodeData.locked as boolean | undefined;

  const update = useCallback(
    (patch: Record<string, unknown>) => {
      updateNodeData(id, { data_json: { ...nodeData, ...patch } });
    },
    [id, nodeData, updateNodeData]
  );

  const handleSplit = useCallback(() => {
    const count = rows * cols;
    const newFrames: SplitFrame[] = Array.from({ length: count }, (_, i) => ({
      index: i,
    }));
    update({ frames: newFrames });
  }, [rows, cols, update]);

  const totalFrames = rows * cols;

  return (
    <NodeWrapper
      nodeId={id}
      title="Split Frames"
      icon={<Scissors size={14} />}
      selected={selected}
      locked={locked}
      accentColor="#f59e0b"
      handles={[
        { type: 'target', position: Position.Left },
        { type: 'source', position: Position.Right },
      ]}
    >
      {sourceImageUrl && (
        <NodeImagePreview imageUrl={sourceImageUrl} alt="Source image" className="mb-1" />
      )}

      {/* Grid selector */}
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <label className={s.label}>Rows</label>
          <input
            type="number"
            min={1}
            max={6}
            className={s.sizeDropdown + ' w-full'}
            value={rows}
            onChange={(e) => update({ rows: Math.max(1, Math.min(6, Number(e.target.value))) })}
          />
        </div>
        <span className="text-gray-500 pt-4">×</span>
        <div className="flex-1">
          <label className={s.label}>Cols</label>
          <input
            type="number"
            min={1}
            max={6}
            className={s.sizeDropdown + ' w-full'}
            value={cols}
            onChange={(e) => update({ cols: Math.max(1, Math.min(6, Number(e.target.value))) })}
          />
        </div>
        <div className="pt-4 flex items-center gap-1 text-xs text-gray-400">
          <Grid3X3 size={12} />
          {totalFrames}
        </div>
      </div>

      <button className={s.generateButton} onClick={handleSplit}>
        <Scissors size={14} />
        Split into {totalFrames} frames
      </button>

      {/* Frame grid */}
      {frames.length > 0 && (
        <div
          className="grid gap-1 mt-1"
          style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}
        >
          {frames.map((frame) => (
            <div
              key={frame.index}
              className="relative bg-gray-800 rounded border border-gray-700 aspect-square flex items-center justify-center overflow-hidden"
            >
              {frame.thumbnailUrl ? (
                <img
                  src={frame.thumbnailUrl}
                  alt={`Frame ${frame.index + 1}`}
                  className="w-full h-full object-cover"
                />
              ) : (
                <span className="text-gray-600 text-[10px]">{frame.index + 1}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </NodeWrapper>
  );
});

export default StoryboardSplitNode;
