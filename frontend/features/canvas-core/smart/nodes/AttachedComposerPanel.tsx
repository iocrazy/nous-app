// features/canvas-core/smart/nodes/AttachedComposerPanel.tsx
//
// IC-parity ⑥ — the FULL attached composer under a selected media / group /
// output card (Infinite's per-node panel: 图片/视频 tabs, 「N 输入图」chips,
// prompt box, model/尺寸/张数 footer, 运行). Replaces the earlier one-key
// Create bar the user called out as too little. All state is LOCAL until
// Run, which spawns a wired prompt node seeded with the panel's values and
// dispatches it — the prompt node then owns the record (retry, status,
// output slots), exactly like a panel-born run in Infinite.

import { Image as ImageIcon, Library, Play, Video } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useLibraryStore } from '../../library/libraryStore';
import { mediaSrc } from '../mediaUrl';
import { createPromptFromNode } from '../recreate';
import { rerunPrompt } from '../regenerate';
import { MentionImageGrid } from './MentionImageGrid';
import { PromptBodyEditor, type PromptBodyEditorHandle } from './PromptBodyEditor';
import type { PromptImageRef } from './promptImageRefs';
import { GenFooterControls } from './GenFooterControls';
import { UiSelect } from '../../../../components/ui';
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
  const { t } = useTranslation();
  const [kind, setKind] = useState<'image' | 'video'>('image');
  const [body, setBody] = useState('');
  const [model, setModel] = useState('');
  const [ratio, setRatio] = useState('auto');
  const [count, setCount] = useState(1);
  const [quality, setQuality] = useState<string | undefined>(undefined);
  const [resolution, setResolution] = useState<string | undefined>(undefined);
  const [duration, setDuration] = useState<number | undefined>(undefined);
  const [videoMode, setVideoMode] = useState<'multimodal' | 'frames' | undefined>(undefined);
  const [engine, setEngine] = useState('');
  const [sourceUrl, setSourceUrl] = useState<string | null>(
    inputUrls[0] ?? null,
  );
  // @-mention over this node's own images. They are already the node's, so
  // mentioning one costs nothing — no import, no URL rewriting. The asset
  // library is deliberately not offered here yet (it is being reworked).
  const [mentionOpen, setMentionOpen] = useState(false);
  const editorRef = useRef<PromptBodyEditorHandle | null>(null);
  const mentionImages = useMemo<PromptImageRef[]>(
    () =>
      inputUrls.map((url, i) => ({ url, alias: `Image ${i + 1}`, kind: 'image' })),
    [inputUrls],
  );
  const allModels = useGenerationModels(kind);
  // actual_provider never reaches the client (2026-08-14 leak tripwire —
  // the field was always null in prod, so this grouping never worked).
  // Until a public grouping key is decided, filter out the ghost values so
  // the dropdown doesn't render an 'undefined' entry.
  const engines = Array.from(
    new Set(allModels.map((m) => (m as { actual_provider?: string }).actual_provider)),
  )
    .filter((e): e is string => Boolean(e))
    .sort();
  const models = engine
    ? allModels.filter((m) => (m as { actual_provider?: string }).actual_provider === engine)
    : allModels;

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
    if (kind === 'video' && resolution)
      (gen as { resolution?: string }).resolution = resolution;
    if (kind === 'video' && videoMode)
      (gen as { video_mode?: string }).video_mode = videoMode;
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
      {/* IC top row: engine dropdown (API生成/GPT CLI/即梦 CLI…) + tabs. */}
      <div className="mb-1.5 flex items-center gap-1">
        <UiSelect
          triggerClassName="nodrag mh-chip max-w-28 focus-visible:ring-1 focus-visible:ring-canvas-strong/40"
          value={engine}
          onChange={(e) => {
            setEngine(e.target.value);
            setModel('');
          }}
          aria-label="Engine"
        >
          <option value="">All engines</option>
          {engines.map((eng) => (
            <option key={eng} value={eng}>
              {eng}
            </option>
          ))}
        </UiSelect>
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
          onClick={() => {
            setKind('video');
            if (ratio === '1:1') setRatio('16:9');
          }}
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

      {/* relative so the mention grid's `bottom-full` anchors to this box */}
      <div className="relative mb-1.5 rounded-lg border border-canvas-line p-2">
        <PromptBodyEditor
          ref={editorRef}
          testId="attached-prompt-editor"
          ariaLabel="Attached prompt"
          value={body}
          onChange={setBody}
          onAtTyped={() => setMentionOpen(true)}
          onMentionQueryChange={(q) => {
            if (q === null) setMentionOpen(false);
          }}
          onKeyDown={(event) => {
            if (mentionOpen && event.key === 'Escape') {
              setMentionOpen(false);
              return true;
            }
            return false;
          }}
          placeholder="Describe what to generate from this…"
        />
        {mentionOpen && (
          <MentionImageGrid
            images={mentionImages}
            onPick={(img) => {
              editorRef.current?.insertImage(img);
              // Mentioning an image also makes it the i2i source, matching
              // what clicking its chip in the input row above does.
              setSourceUrl(img.url);
              setMentionOpen(false);
            }}
          />
        )}
      </div>
      {/* No target: the composer is not a prompt NODE yet — Run is what mints
          one — so there is nothing for the panel's target bar to aim at. It
          opens on Prompts and the user copies from there. */}
      <button
        type="button"
        data-testid="composer-library"
        aria-label={t('canvas.library.promptTemplates', 'Prompt Templates')}
        onClick={() => useLibraryStore.getState().openPanel({ page: 'prompts' })}
        className="nodrag absolute right-3 top-16 flex h-6 w-6 items-center justify-center rounded border border-canvas-line text-canvas-muted hover:text-canvas-text"
      >
        <Library size={12} />
      </button>

      <div className="flex min-w-0 items-center gap-1">
        <GenFooterControls
          gen={
            (kind === 'image'
              ? { kind: 'image', model, ratio, count, quality, resolution }
              : { kind: 'video', model, aspect: ratio, duration, resolution, video_mode: videoMode }) as never
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
            if ('video_mode' in g) setVideoMode(g.video_mode);
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
      {/* IC flat option rows (用户: pill 弹层里的选项"看不见" — IC 把
          分辨率/参考模式全部平铺在面板上). Only real jimeng-CLI channels
          appear; the greyed IC checkboxes without a backend stay out. */}
      {kind === 'video' && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          {([
            ['multimodal', 'Omni Ref', 'All wired refs guide the clip'],
            ['frames', 'First & Last', 'Refs 1+2 become the end frames'],
          ] as const).map(([mode, label, hint]) => (
            <label
              key={mode}
              data-testid={`composer-mode-${mode}`}
              title={hint}
              className={`nodrag flex cursor-pointer items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${
                videoMode === mode
                  ? 'border-canvas-strong bg-canvas-strong text-canvas-card'
                  : 'border-canvas-line text-canvas-text'
              }`}
            >
              <input
                type="checkbox"
                checked={videoMode === mode}
                onChange={() => setVideoMode(videoMode === mode ? undefined : mode)}
                className="nodrag h-3 w-3 accent-current"
              />
              {label}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
