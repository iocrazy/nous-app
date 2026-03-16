import React, { useCallback, useEffect, useRef } from 'react';
import { NodeProps, Position } from '@xyflow/react';
import { Layers, Plus, Minus, Wand2 } from 'lucide-react';
import NodeWrapper from './shared/NodeWrapper';
import NodeImagePreview from './shared/NodeImagePreview';
import { nodeControlStyles as s } from './shared/NodeControlStyles';
import { useStoryboardStore } from '../../../stores/storyboardStore';

// ─── Types ────────────────────────────────────────────────────────────────────

interface FrameEntry {
  id: string;
  description: string;
  imageUrl?: string;
  progress?: number;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const MODEL_OPTIONS = [
  { value: 'flux-pro', label: 'FLUX Pro' },
  { value: 'stable-diffusion-xl', label: 'SDXL' },
  { value: 'dall-e-3', label: 'DALL-E 3' },
];

const PROVIDER_OPTIONS = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'replicate', label: 'Replicate' },
  { value: 'fal', label: 'fal.ai' },
];

// ─── StoryboardGenNode ────────────────────────────────────────────────────────

const StoryboardGenNode = React.memo(function StoryboardGenNode({ id, selected, data }: NodeProps) {
  const { updateNodeData } = useStoryboardStore();

  const nodeData = data as Record<string, unknown>;
  const frames = (nodeData.frames as FrameEntry[]) ?? [{ id: '1', description: '' }];
  const model = (nodeData.model as string) ?? 'flux-pro';
  const provider = (nodeData.provider as string) ?? 'replicate';
  const locked = nodeData.locked as boolean | undefined;

  const generateIntervalsRef = useRef<ReturnType<typeof setInterval>[]>([]);

  // Clean up intervals on unmount
  useEffect(() => {
    return () => {
      for (const interval of generateIntervalsRef.current) {
        clearInterval(interval);
      }
      generateIntervalsRef.current = [];
    };
  }, []);

  const update = useCallback(
    (patch: Record<string, unknown>) => {
      updateNodeData(id, { data_json: { ...nodeData, ...patch } });
    },
    [id, nodeData, updateNodeData]
  );

  const addFrame = useCallback(() => {
    const newFrame: FrameEntry = { id: `frame-${Date.now()}`, description: '' };
    update({ frames: [...frames, newFrame] });
  }, [frames, update]);

  const removeFrame = useCallback(
    (frameId: string) => {
      if (frames.length <= 1) return;
      update({ frames: frames.filter((f) => f.id !== frameId) });
    },
    [frames, update]
  );

  const updateFrameDescription = useCallback(
    (frameId: string, description: string) => {
      update({
        frames: frames.map((f) => (f.id === frameId ? { ...f, description } : f)),
      });
    },
    [frames, update]
  );

  const handleBatchGenerate = useCallback(() => {
    // Clear any existing intervals
    for (const interval of generateIntervalsRef.current) {
      clearInterval(interval);
    }
    generateIntervalsRef.current = [];

    // Mark all frames as generating
    update({
      frames: frames.map((f) => ({ ...f, progress: 0 })),
    });
    // Simulate — real implementation calls API per frame
    frames.forEach((frame, idx) => {
      let p = 0;
      const interval = setInterval(() => {
        p += 10;
        if (p >= 100) {
          clearInterval(interval);
          generateIntervalsRef.current = generateIntervalsRef.current.filter((i) => i !== interval);
          update({
            frames: frames.map((f) =>
              f.id === frame.id ? { ...f, progress: 100 } : f
            ),
          });
        } else {
          setTimeout(() => {}, idx * 500); // stagger
        }
      }, 200 + idx * 100);
      generateIntervalsRef.current.push(interval);
    });
  }, [frames, update]);

  const canGenerate = frames.some((f) => f.description.trim().length > 0);

  return (
    <NodeWrapper
      nodeId={id}
      title="Generate Storyboard"
      icon={<Layers size={14} />}
      selected={selected}
      locked={locked}
      accentColor="#10b981"
      handles={[
        { type: 'target', position: Position.Left },
        { type: 'source', position: Position.Right },
      ]}
    >
      {/* Model / Provider */}
      <div className="flex gap-2">
        <div className="flex-1">
          <label className={s.label}>Model</label>
          <select className={s.modelSelector} value={model} onChange={(e) => update({ model: e.target.value })}>
            {MODEL_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex-1">
          <label className={s.label}>Provider</label>
          <select className={s.sizeDropdown + ' w-full'} value={provider} onChange={(e) => update({ provider: e.target.value })}>
            {PROVIDER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
      </div>

      {/* Frame descriptions */}
      <div className={s.section}>
        <div className="flex items-center justify-between mb-1">
          <label className={s.label + ' mb-0'}>Frame Descriptions</label>
          <button onClick={addFrame} className="text-gray-400 hover:text-green-400 transition-colors p-0.5 rounded">
            <Plus size={13} />
          </button>
        </div>
        <div className="space-y-2 max-h-[240px] overflow-y-auto pr-1">
          {frames.map((frame, idx) => (
            <div key={frame.id} className="space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-500">Frame {idx + 1}</span>
                <button
                  onClick={() => removeFrame(frame.id)}
                  disabled={frames.length <= 1}
                  className="text-gray-600 hover:text-red-400 transition-colors disabled:opacity-30"
                >
                  <Minus size={11} />
                </button>
              </div>
              <textarea
                className={s.textarea}
                rows={2}
                placeholder={`Describe frame ${idx + 1}...`}
                value={frame.description}
                onChange={(e) => updateFrameDescription(frame.id, e.target.value)}
              />
              {frame.imageUrl || (frame.progress !== undefined && frame.progress < 100) ? (
                <NodeImagePreview imageUrl={frame.imageUrl} progress={frame.progress} className="h-20" />
              ) : null}
            </div>
          ))}
        </div>
      </div>

      <button
        className={s.generateButton}
        onClick={handleBatchGenerate}
        disabled={!canGenerate}
      >
        <Wand2 size={14} />
        Generate {frames.length} Frame{frames.length > 1 ? 's' : ''}
      </button>
    </NodeWrapper>
  );
});

export default StoryboardGenNode;
