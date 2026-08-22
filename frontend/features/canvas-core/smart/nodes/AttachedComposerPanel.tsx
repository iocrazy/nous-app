// features/canvas-core/smart/nodes/AttachedComposerPanel.tsx
//
// IC-parity ⑥ — the FULL attached composer under a selected media / group /
// output card (Infinite's per-node panel: 图片/视频 tabs, 「N 输入图」chips,
// prompt box, model/尺寸/张数 footer, 运行). Replaces the earlier one-key
// Create bar the user called out as too little. All state is LOCAL until
// Run, which spawns a wired prompt node seeded with the panel's values and
// dispatches it — the prompt node then owns the record (retry, status,
// output slots), exactly like a panel-born run in Infinite.

import { Image as ImageIcon, Play, Video } from 'lucide-react';
import { useState } from 'react';

import { mediaSrc } from '../mediaUrl';
import { createPromptFromNode } from '../recreate';
import { rerunPrompt } from '../regenerate';
import { GenFooterControls } from './GenFooterControls';
import { useGenerationModels } from './useGenerationModels';

export interface AttachedComposerPanelProps {
  nodeId: string;
  /** The node's own durable images (media/group items, output images). */
  inputUrls: string[];
  /** Pin visible (the node is selected); otherwise hover reveals. */
  pinned?: boolean;
  readOnly?: boolean;
}

export function AttachedComposerPanel({
  nodeId,
  inputUrls,
  pinned,
  readOnly,
}: AttachedComposerPanelProps) {
  const [kind, setKind] = useState<'image' | 'video'>('image');
  const [body, setBody] = useState('');
  const [model, setModel] = useState('');
  const [ratio, setRatio] = useState('1:1');
  const [count, setCount] = useState(1);
  const [quality, setQuality] = useState<string | undefined>(undefined);
  const [resolution, setResolution] = useState<string | undefined>(undefined);
  const [duration, setDuration] = useState<number | undefined>(undefined);
  const [sourceUrl, setSourceUrl] = useState<string | null>(
    inputUrls[0] ?? null,
  );
  const models = useGenerationModels(kind);

  // Unlike the small hover toolbars, the FULL panel renders only while the
  // node is selected (IC behaviour) — a large panel popping in on hover is
  // noise, and unselected nodes shouldn't carry its DOM at all.
  if (readOnly || !pinned) return null;

  const run = () => {
    const gen =
      kind === 'image'
        ? { kind: 'image' as const, model, ratio, count }
        : { kind: 'video' as const, model, aspect: ratio };
    if (kind === 'image' && quality) (gen as { quality?: string }).quality = quality;
    if (kind === 'image' && resolution)
      (gen as { resolution?: string }).resolution = resolution;
    if (kind === 'video' && duration)
      (gen as { duration?: number }).duration = duration;
    const promptId = createPromptFromNode(nodeId, {
      body,
      gen,
      ...(sourceUrl ? { source_ref: sourceUrl } : {}),
    });
    if (promptId) {
      void rerunPrompt(promptId);
      setBody('');
    }
  };

  return (
    <div
      data-testid="attached-composer"
      className="canvas-island absolute left-1/2 top-full z-10 mt-2 w-[26rem] -translate-x-1/2 rounded-xl p-2.5"
    >
      {/* Image | Video pills (IC 图片/视频 tabs). */}
      <div className="mb-1.5 flex items-center gap-1">
        <button
          type="button"
          data-testid="composer-kind-image"
          onClick={() => setKind('image')}
          className={`nodrag flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${
            kind === 'image'
              ? 'border-canvas-strong bg-canvas-strong text-canvas-card'
              : 'border-canvas-line text-canvas-text'
          }`}
        >
          <ImageIcon size={11} />
          Image
        </button>
        <button
          type="button"
          data-testid="composer-kind-video"
          onClick={() => setKind('video')}
          className={`nodrag flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${
            kind === 'video'
              ? 'border-canvas-strong bg-canvas-strong text-canvas-card'
              : 'border-canvas-line text-canvas-text'
          }`}
        >
          <Video size={11} />
          Video
        </button>
      </div>

      {/* 「N 输入图」chips: this node's own images; the highlighted one is
          the i2i source (defaults to the first, click to switch). */}
      {inputUrls.length > 0 && (
        <div
          data-testid="composer-input-row"
          className="mb-1.5 flex items-center gap-1.5"
        >
          {inputUrls.slice(0, 8).map((url, i) => (
            <button
              key={url}
              type="button"
              data-testid="composer-input-thumb"
              title={`Image ${i + 1}`}
              onClick={() => setSourceUrl(sourceUrl === url ? null : url)}
              className={`nodrag relative h-7 w-7 shrink-0 overflow-hidden rounded border ${
                sourceUrl === url
                  ? 'border-canvas-strong ring-1 ring-canvas-strong'
                  : 'border-canvas-line/60'
              }`}
            >
              <img
                src={mediaSrc(url)}
                alt={`Image ${i + 1}`}
                className="h-full w-full object-cover"
              />
              {/* IC 图N corner badge */}
              <span className="pointer-events-none absolute left-0 top-0 rounded-br-md bg-canvas-strong px-1 text-[8px] font-bold leading-3 text-canvas-card">{i + 1}</span>
            </button>
          ))}
          <span className="text-[10px] font-semibold text-canvas-muted">
            {inputUrls.length} inputs
          </span>
        </div>
      )}

      <textarea
        className="nodrag nowheel mb-1.5 min-h-[3rem] w-full resize-y rounded-lg border border-canvas-line bg-transparent p-2 text-xs text-canvas-text outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40"
        placeholder="Describe what to generate from this…"
        aria-label="Attached prompt"
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={3}
      />

      <div className="flex min-w-0 items-center gap-1">
        <GenFooterControls
          gen={
            (kind === 'image'
              ? { kind: 'image', model, ratio, count, quality, resolution }
              : { kind: 'video', model, aspect: ratio, duration }) as never
          }
          models={models}
          onChange={(g) => {
            if (g.model !== undefined) setModel(g.model);
            if (g.ratio !== undefined) setRatio(g.ratio);
            if (g.aspect !== undefined) setRatio(g.aspect);
            if (g.count !== undefined) setCount(g.count);
            if ('quality' in g) setQuality(g.quality);
            if ('resolution' in g) setResolution(g.resolution);
            if ('duration' in g) setDuration(g.duration);
          }}
        />
        <button
          type="button"
          data-testid="composer-run"
          aria-label="Run"
          onClick={run}
          className="nodrag ml-auto flex shrink-0 items-center gap-1 rounded-full border border-transparent bg-canvas-strong px-2.5 py-1 text-xs font-bold text-canvas-card hover:opacity-90"
        >
          <Play size={11} />
          Run
        </button>
      </div>
    </div>
  );
}
