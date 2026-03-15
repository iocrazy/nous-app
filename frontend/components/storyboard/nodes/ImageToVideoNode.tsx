import React, { useCallback, useRef } from 'react';
import { NodeProps, Position } from '@xyflow/react';
import { Video, Play, Pause } from 'lucide-react';
import NodeWrapper from './shared/NodeWrapper';
import NodeImagePreview from './shared/NodeImagePreview';
import { nodeControlStyles as s } from './shared/NodeControlStyles';
import { useStoryboardStore } from '../../../stores/storyboardStore';

// ─── Constants ────────────────────────────────────────────────────────────────

const PROVIDERS = [
  { value: 'kling', label: 'Kling' },
  { value: 'runway', label: 'Runway' },
  { value: 'vidu', label: 'Vidu' },
];

const DURATIONS = [
  { value: '3', label: '3s' },
  { value: '5', label: '5s' },
  { value: '10', label: '10s' },
];

const INTENSITIES = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
];

// ─── ImageToVideoNode ─────────────────────────────────────────────────────────

const ImageToVideoNode = React.memo(function ImageToVideoNode({ id, selected, data }: NodeProps) {
  const { updateNodeData } = useStoryboardStore();
  const videoRef = useRef<HTMLVideoElement>(null);

  const nodeData = data as Record<string, unknown>;
  const sourceImageUrl = nodeData.sourceImageUrl as string | undefined;
  const motionPrompt = (nodeData.motionPrompt as string) ?? '';
  const provider = (nodeData.provider as string) ?? 'kling';
  const duration = (nodeData.duration as string) ?? '5';
  const intensity = (nodeData.intensity as string) ?? 'medium';
  const videoUrl = nodeData.videoUrl as string | undefined;
  const progress = nodeData.progress as number | undefined;
  const locked = nodeData.locked as boolean | undefined;

  const update = useCallback(
    (patch: Record<string, unknown>) => {
      updateNodeData(id, { data_json: { ...nodeData, ...patch } });
    },
    [id, nodeData, updateNodeData]
  );

  const handleGenerate = useCallback(() => {
    update({ progress: 0, videoUrl: undefined });
    let p = 0;
    const interval = setInterval(() => {
      p += 5;
      if (p >= 100) {
        clearInterval(interval);
        update({ progress: 100 });
      } else {
        update({ progress: p });
      }
    }, 300);
  }, [update]);

  const togglePlay = useCallback(() => {
    if (!videoRef.current) return;
    if (videoRef.current.paused) {
      videoRef.current.play();
    } else {
      videoRef.current.pause();
    }
  }, []);

  const isGenerating = progress !== undefined && progress < 100;

  return (
    <NodeWrapper
      nodeId={id}
      title="Image to Video"
      icon={<Video size={14} />}
      selected={selected}
      locked={locked}
      accentColor="#ec4899"
      handles={[
        { type: 'target', position: Position.Left },
        { type: 'source', position: Position.Right },
      ]}
    >
      {sourceImageUrl && !videoUrl && (
        <NodeImagePreview imageUrl={sourceImageUrl} alt="Source image" />
      )}

      {/* Video preview */}
      {videoUrl && (
        <div className="relative rounded-lg overflow-hidden bg-gray-800 border border-gray-700">
          <video
            ref={videoRef}
            src={videoUrl}
            className="w-full rounded-lg"
            loop
            playsInline
          />
          <button
            onClick={togglePlay}
            className="absolute inset-0 flex items-center justify-center bg-black/30 opacity-0 hover:opacity-100 transition-opacity"
          >
            <Play size={28} className="text-white" />
          </button>
        </div>
      )}

      {isGenerating && (
        <div>
          <div className={s.progressBar.container}>
            <div className={s.progressBar.fill} style={{ width: `${progress}%` }} />
          </div>
          <p className="text-xs text-gray-400 mt-1 text-center">{progress}% — Generating video...</p>
        </div>
      )}

      {/* Motion prompt */}
      <div className={s.section}>
        <label className={s.label}>Motion Prompt</label>
        <textarea
          className={s.textarea}
          rows={2}
          placeholder="Describe the motion..."
          value={motionPrompt}
          onChange={(e) => update({ motionPrompt: e.target.value })}
        />
      </div>

      {/* Provider + Duration + Intensity */}
      <div className="grid grid-cols-3 gap-2">
        <div>
          <label className={s.label}>Provider</label>
          <select className={s.sizeDropdown + ' w-full'} value={provider} onChange={(e) => update({ provider: e.target.value })}>
            {PROVIDERS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
          </select>
        </div>
        <div>
          <label className={s.label}>Duration</label>
          <select className={s.sizeDropdown + ' w-full'} value={duration} onChange={(e) => update({ duration: e.target.value })}>
            {DURATIONS.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
          </select>
        </div>
        <div>
          <label className={s.label}>Motion</label>
          <select className={s.sizeDropdown + ' w-full'} value={intensity} onChange={(e) => update({ intensity: e.target.value })}>
            {INTENSITIES.map((i) => <option key={i.value} value={i.value}>{i.label}</option>)}
          </select>
        </div>
      </div>

      <button
        className={s.generateButton}
        onClick={handleGenerate}
        disabled={isGenerating}
      >
        <Video size={14} />
        {isGenerating ? 'Generating...' : 'Generate Video'}
      </button>
    </NodeWrapper>
  );
});

export default ImageToVideoNode;
