import React, { useCallback, useEffect, useRef } from 'react';
import { NodeProps, Position } from '@xyflow/react';
import { ImageIcon, Wand2, Users } from 'lucide-react';
import NodeWrapper from './shared/NodeWrapper';
import NodeImagePreview from './shared/NodeImagePreview';
import { nodeControlStyles as s } from './shared/NodeControlStyles';
import { useStoryboardStore } from '../../../stores/storyboardStore';

// ─── Constants ────────────────────────────────────────────────────────────────

const MODEL_OPTIONS = [
  { value: 'flux-pro', label: 'FLUX Pro' },
  { value: 'stable-diffusion-xl', label: 'Stable Diffusion XL' },
  { value: 'dall-e-3', label: 'DALL-E 3' },
  { value: 'midjourney', label: 'Midjourney' },
];

const ASPECT_RATIOS = ['1:1', '4:3', '16:9', '9:16', '3:2', '2:3'];

// ─── ImageEditNode ────────────────────────────────────────────────────────────

const ImageEditNode = React.memo(function ImageEditNode({ id, selected, data }: NodeProps) {
  const { updateNodeData, characters } = useStoryboardStore();

  const nodeData = data as Record<string, unknown>;
  const prompt = (nodeData.prompt as string) ?? '';
  const model = (nodeData.model as string) ?? 'flux-pro';
  const aspectRatio = (nodeData.aspectRatio as string) ?? '1:1';
  const selectedChars = (nodeData.selectedChars as string[]) ?? [];
  const resultImageUrl = nodeData.resultImageUrl as string | undefined;
  const progress = nodeData.progress as number | undefined;
  const locked = nodeData.locked as boolean | undefined;

  const update = useCallback(
    (patch: Record<string, unknown>) => {
      updateNodeData(id, { data_json: { ...nodeData, ...patch } });
    },
    [id, nodeData, updateNodeData]
  );

  const generateIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Clean up interval on unmount
  useEffect(() => {
    return () => {
      if (generateIntervalRef.current) clearInterval(generateIntervalRef.current);
    };
  }, []);

  const toggleCharacter = useCallback(
    (charId: string) => {
      const next = selectedChars.includes(charId)
        ? selectedChars.filter((c) => c !== charId)
        : [...selectedChars, charId];
      update({ selectedChars: next });
    },
    [selectedChars, update]
  );

  const handleGenerate = useCallback(() => {
    update({ progress: 0 });
    // Simulate progress — real implementation wires to API
    let p = 0;
    if (generateIntervalRef.current) clearInterval(generateIntervalRef.current);
    generateIntervalRef.current = setInterval(() => {
      p += 10;
      if (p >= 100) {
        if (generateIntervalRef.current) clearInterval(generateIntervalRef.current);
        generateIntervalRef.current = null;
        update({ progress: 100 });
      } else {
        update({ progress: p });
      }
    }, 200);
  }, [update]);

  return (
    <NodeWrapper
      nodeId={id}
      title="Image Edit"
      icon={<ImageIcon size={14} />}
      selected={selected}
      locked={locked}
      accentColor="#8b5cf6"
      handles={[
        { type: 'target', position: Position.Left },
        { type: 'source', position: Position.Right },
      ]}
    >
      <NodeImagePreview imageUrl={resultImageUrl} progress={progress} />

      <div className={s.section}>
        <label className={s.label}>Prompt</label>
        <textarea
          className={s.textarea}
          rows={3}
          placeholder="Describe the edit..."
          value={prompt}
          onChange={(e) => update({ prompt: e.target.value })}
        />
      </div>

      <div className="flex gap-2">
        <div className="flex-1">
          <label className={s.label}>Model</label>
          <select
            className={s.modelSelector}
            value={model}
            onChange={(e) => update({ model: e.target.value })}
          >
            {MODEL_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
        <div className="w-24">
          <label className={s.label}>Ratio</label>
          <select
            className={s.sizeDropdown}
            value={aspectRatio}
            onChange={(e) => update({ aspectRatio: e.target.value })}
          >
            {ASPECT_RATIOS.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
        </div>
      </div>

      {characters.length > 0 && (
        <div className={s.section}>
          <label className={s.label}>
            <Users size={10} className="inline mr-1" />
            Characters
          </label>
          <div className="flex flex-wrap gap-1">
            {characters.map((char) => (
              <button
                key={char.id}
                onClick={() => toggleCharacter(char.id)}
                className={[
                  'px-2 py-0.5 rounded-full text-xs border transition-colors',
                  selectedChars.includes(char.id)
                    ? 'bg-purple-600 border-purple-500 text-white'
                    : 'bg-gray-800 border-gray-600 text-gray-300 hover:border-gray-500',
                ].join(' ')}
              >
                {char.name}
              </button>
            ))}
          </div>
        </div>
      )}

      <button
        className={s.generateButton}
        onClick={handleGenerate}
        disabled={!prompt.trim() || (progress !== undefined && progress < 100)}
      >
        <Wand2 size={14} />
        {progress !== undefined && progress < 100 ? 'Generating...' : 'Generate'}
      </button>
    </NodeWrapper>
  );
});

export default ImageEditNode;
