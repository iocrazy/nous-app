import React, { useCallback, useState } from 'react';
import { NodeProps, Position, useReactFlow } from '@xyflow/react';
import { Scissors, Grid3X3, Loader2, AlertCircle } from 'lucide-react';
import NodeWrapper from './shared/NodeWrapper';
import NodeImagePreview from './shared/NodeImagePreview';
import { nodeControlStyles as s } from './shared/NodeControlStyles';
import { useStoryboardStore } from '../../../stores/storyboardStore';
import { splitImage, SplitImageFrame } from '../../../services/storyboardService';

// ─── Types ────────────────────────────────────────────────────────────────────

interface SplitFrame {
  index: number;
  thumbnailUrl?: string;
  imageUrl?: string;
  assetId?: string;
  frameId?: string;
  row?: number;
  col?: number;
}

// ─── StoryboardSplitNode ──────────────────────────────────────────────────────

const StoryboardSplitNode = React.memo(function StoryboardSplitNode({ id, selected, data }: NodeProps) {
  const { updateNodeData } = useStoryboardStore();
  const currentProjectId = useStoryboardStore((s) => s.currentProjectId);
  const { getEdges } = useReactFlow();

  const [splitting, setSplitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const nodeData = data as Record<string, unknown>;
  const rows = (nodeData.rows as number) ?? 2;
  const cols = (nodeData.cols as number) ?? 3;
  const frames = (nodeData.frames as SplitFrame[]) ?? [];
  const sourceImageUrl = nodeData.sourceImageUrl as string | undefined;
  const sourceAssetId = nodeData.sourceAssetId as string | undefined;
  const locked = nodeData.locked as boolean | undefined;

  const update = useCallback(
    (patch: Record<string, unknown>) => {
      updateNodeData(id, { data_json: { ...nodeData, ...patch } });
    },
    [id, nodeData, updateNodeData]
  );

  // Resolve source asset from connected UploadNode via edges
  const resolveSourceAssetId = useCallback((): string | undefined => {
    if (sourceAssetId) return sourceAssetId;

    // Look for incoming edges to find the source node
    const edges = getEdges();
    const incomingEdge = edges.find((e) => e.target === id);
    if (!incomingEdge) return undefined;

    // The source node should have an asset_id in its data
    return undefined; // Caller must set sourceAssetId explicitly via node data
  }, [id, sourceAssetId, getEdges]);

  const handleSplit = useCallback(async () => {
    const assetId = resolveSourceAssetId();
    if (!assetId) {
      setError('No source image. Upload an image or connect an Upload node first.');
      return;
    }
    if (!currentProjectId) {
      setError('No project selected.');
      return;
    }

    setSplitting(true);
    setError(null);

    try {
      const result = await splitImage(currentProjectId, assetId, rows, cols, id);
      const newFrames: SplitFrame[] = result.frames.map((f: SplitImageFrame) => ({
        index: f.frame_index,
        thumbnailUrl: f.preview_url,
        imageUrl: f.image_url,
        assetId: f.asset_id,
        frameId: f.id,
        row: f.row,
        col: f.col,
      }));
      update({
        frames: newFrames,
        splitResult: result,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Split failed';
      setError(message);
      console.error('[SplitNode] split failed:', err);
    } finally {
      setSplitting(false);
    }
  }, [resolveSourceAssetId, currentProjectId, rows, cols, id, update]);

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
        <div className="relative mb-1">
          <NodeImagePreview imageUrl={sourceImageUrl} alt="Source image" className="mb-0" />
          {/* Grid overlay on source image */}
          <svg
            className="absolute inset-0 w-full h-full pointer-events-none"
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
          >
            {Array.from({ length: cols - 1 }, (_, i) => (
              <line
                key={`v-${i}`}
                x1={((i + 1) / cols) * 100}
                y1={0}
                x2={((i + 1) / cols) * 100}
                y2={100}
                stroke="rgba(245,158,11,0.6)"
                strokeWidth="0.5"
              />
            ))}
            {Array.from({ length: rows - 1 }, (_, i) => (
              <line
                key={`h-${i}`}
                x1={0}
                y1={((i + 1) / rows) * 100}
                x2={100}
                y2={((i + 1) / rows) * 100}
                stroke="rgba(245,158,11,0.6)"
                strokeWidth="0.5"
              />
            ))}
          </svg>
        </div>
      )}

      {/* Grid selector */}
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <label className={s.label}>Rows</label>
          <input
            type="number"
            min={1}
            max={10}
            className={s.sizeDropdown + ' w-full'}
            value={rows}
            onChange={(e) => update({ rows: Math.max(1, Math.min(10, Number(e.target.value))) })}
            disabled={splitting}
          />
        </div>
        <span className="text-gray-500 pt-4">&times;</span>
        <div className="flex-1">
          <label className={s.label}>Cols</label>
          <input
            type="number"
            min={1}
            max={10}
            className={s.sizeDropdown + ' w-full'}
            value={cols}
            onChange={(e) => update({ cols: Math.max(1, Math.min(10, Number(e.target.value))) })}
            disabled={splitting}
          />
        </div>
        <div className="pt-4 flex items-center gap-1 text-xs text-gray-400">
          <Grid3X3 size={12} />
          {totalFrames}
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-1.5 text-xs text-red-400 bg-red-950/30 rounded px-2 py-1.5 mt-1">
          <AlertCircle size={12} className="shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      <button
        className={s.generateButton}
        onClick={handleSplit}
        disabled={splitting || !sourceAssetId}
      >
        {splitting ? (
          <>
            <Loader2 size={14} className="animate-spin" />
            Splitting...
          </>
        ) : (
          <>
            <Scissors size={14} />
            Split into {totalFrames} frames
          </>
        )}
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
