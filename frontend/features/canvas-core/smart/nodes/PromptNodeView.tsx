import { useCallback, useEffect, useState } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { PromptGenSettings, PromptNodeData, PromptResourceRef } from '../types';
import { RUN_STATUS_TONE, SMART_NODE_DEFAULT_WIDTH } from '../types';
import { useGenerationModels } from './useGenerationModels';
import { useNodeDataPatch } from './useNodeDataPatch';
import { useCanvasMentionPicker } from './useCanvasMentionPicker';
import { CanvasMentionPicker } from './CanvasMentionPicker';
import { useResourceSearch } from '../../../../hooks/useResourceSearch';
import type { ResourceSearchResult } from '../../../../types';

/**
 * Provider options for the dropdown. Bare model IDs use the existing
 * `get_adapter()` prefix dispatch; `nous/<workflow>` triggers the
 * nous-center routing in the canvas-run service (#610).
 *
 * Kept small for now — the AI Library settings page will own the
 * full per-team provider catalogue in a future slice.
 */
const PROVIDER_OPTIONS: ReadonlyArray<{ slug: string; label: string }> = [
  { slug: '', label: 'Default (qwen-plus)' },
  { slug: 'qwen/qwen-plus', label: 'Qwen Plus' },
  { slug: 'qwen/qwen-turbo', label: 'Qwen Turbo' },
  { slug: 'claude-sonnet-4-6', label: 'Claude Sonnet 4.6' },
  { slug: 'nous/storyboard', label: 'nous-center · storyboard' },
];

type ActiveKind = '' | 'video' | 'image' | 'doc' | 'audio' | 'pdf';

/** Image aspect presets — the Infinite composer's core set. */
const RATIO_OPTIONS = ['1:1', '16:9', '9:16', '4:3', '3:4'] as const;

export function PromptNodeView({ id, data, selected }: NodeProps) {
  const {
    body,
    provider_slug,
    run_status,
    run_error,
    resource_refs = [],   // default [] for nodes persisted before this field
    gen = null,           // absent = legacy text prompt
  } = data as unknown as PromptNodeData;
  const patch = useNodeDataPatch(id);
  const genKind = gen?.kind ?? 'text';
  const genModels = useGenerationModels(gen ? gen.kind : undefined);

  // Kind filter for the @-mention picker tabs (All / Video / Image / Doc …)
  const [activeKind, setActiveKind] = useState<ActiveKind>('');

  const haloTone = RUN_STATUS_TONE[run_status];

  // ── @-mention handler ────────────────────────────────────────────────────
  // Builds a PromptResourceRef from the picked SearchResult and appends it
  // to resource_refs (deduplicated by resource_id).
  const handleSelectRef = useCallback(
    (item: ResourceSearchResult) => {
      const ref: PromptResourceRef = {
        resource_id: item.id,
        name: item.name,
        kind: item.kind,
        mime: item.mime ?? '',
        scope: item.scope,
      };
      const current = resource_refs as PromptResourceRef[];
      if (current.some((r) => r.resource_id === ref.resource_id)) return;
      patch({ resource_refs: [...current, ref] });
    },
    [resource_refs, patch],
  );

  const mention = useCanvasMentionPicker({
    value: body,
    onValueChange: (v) => patch({ body: v }),
    onSelectRef: handleSelectRef,
  });

  // Search for resources whenever the picker is open (debounced inside the hook).
  // Pass '' when picker is closed so cached data is reused on next open.
  const { data: searchData, loading: searchLoading } = useResourceSearch(
    mention.pickerOpen ? mention.query : '',
    activeKind,
  );

  // Keep keyboard-wrap bound tight: update the hook's itemCountRef whenever
  // results change. Uses a ref internally so this never triggers re-renders.
  useEffect(() => {
    mention.setItemCount(searchData.results.length);
  }, [searchData.results.length, mention.setItemCount]);

  // ────────────────────────────────────────────────────────────────────────

  return (
    <div
      data-testid="smart-prompt-node"
      className={`mh-node ${haloTone} ${selected ? 'mh-node-selected' : ''}`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.prompt }}
    >
      <Handle
        type="target"
        position={Position.Left}
      />
      <div className="mh-node-head">
        <div className="mh-node-title">Prompt</div>
        <div className="flex items-center gap-1.5">
          {/* Text stays the legacy LLM path; Image/Video route Run through
              the G4-B1 generation tasks (Infinite composer's kind toggle). */}
          <select
            className="nodrag mh-chip outline-none focus:ring-1 focus:ring-canvas-strong/40"
            value={genKind}
            onChange={(e) => {
              const next = e.target.value;
              if (next === 'text') {
                patch({ gen: null });
              } else {
                const settings: PromptGenSettings =
                  next === 'image'
                    ? { kind: 'image', model: gen?.model ?? '', ratio: gen?.ratio ?? '1:1', count: gen?.count ?? 1 }
                    : { kind: 'video', model: gen?.model ?? '', aspect: gen?.aspect ?? '16:9' };
                patch({ gen: settings });
              }
            }}
            aria-label="Prompt kind"
          >
            <option value="text">Text</option>
            <option value="image">Image</option>
            <option value="video">Video</option>
          </select>
          <div className="text-[10px] uppercase tracking-wider text-ink-400">
            {run_status}
          </div>
        </div>
      </div>
      {/* relative so the CanvasMentionPicker's `bottom-full` positions above this section */}
      <div className="relative p-3">
        <textarea
          // nodrag → React Flow doesn't start a drag from this input
          // nowheel → wheel events scroll the textarea instead of zooming canvas
          className="nodrag nowheel min-h-[3.5rem] w-full resize-y bg-transparent text-sm text-ink-800 outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40 dark:text-ink-200"
          placeholder="What should the model generate? Type @ to reference an asset"
          value={body}
          onChange={mention.handleChange}
          onKeyDown={mention.handleKeyDown}
          onBlur={mention.closePicker}
          aria-label="Prompt body"
          rows={3}
        />

        {mention.pickerOpen && (
          <CanvasMentionPicker
            items={searchData.results}
            query={mention.query}
            loading={searchLoading}
            counts={searchData.counts}
            activeKind={activeKind}
            onKindChange={setActiveKind}
            onSelect={mention.handleSelect}
            activeIndex={mention.activeIndex}
          />
        )}

        <div className="mt-2 flex items-center justify-between gap-2 text-xs">
          {!gen && (
            <select
              className="nodrag flex-1 truncate rounded-full border border-canvas-line bg-transparent px-2.5 py-0.5 text-xs text-canvas-text outline-none focus:ring-1 focus:ring-canvas-strong/40"
              value={provider_slug}
              onChange={(e) => patch({ provider_slug: e.target.value })}
              aria-label="Prompt provider"
            >
              {PROVIDER_OPTIONS.map((opt) => (
                <option key={opt.slug || '_default'} value={opt.slug}>
                  {opt.label}
                </option>
              ))}
            </select>
          )}
          {gen && (
            <div className="flex flex-1 items-center gap-1.5">
              <select
                className="nodrag min-w-0 flex-1 truncate rounded-full border border-canvas-line bg-transparent px-2.5 py-0.5 text-xs text-canvas-text outline-none focus:ring-1 focus:ring-canvas-strong/40"
                value={gen.model}
                onChange={(e) => patch({ gen: { ...gen, model: e.target.value } })}
                aria-label="Generation model"
              >
                <option value="">Catalog default</option>
                {genModels.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.display_name || m.name}
                  </option>
                ))}
              </select>
              {gen.kind === 'image' && (
                <>
                  <select
                    className="nodrag rounded-full border border-canvas-line bg-transparent px-2.5 py-0.5 text-xs text-canvas-text outline-none focus:ring-1 focus:ring-canvas-strong/40"
                    value={gen.ratio ?? '1:1'}
                    onChange={(e) => patch({ gen: { ...gen, ratio: e.target.value } })}
                    aria-label="Aspect ratio"
                  >
                    {RATIO_OPTIONS.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                  <input
                    type="number"
                    min={1}
                    max={8}
                    className="nodrag w-12 rounded-full border border-canvas-line bg-transparent px-2.5 py-0.5 text-xs text-canvas-text outline-none focus:ring-1 focus:ring-canvas-strong/40"
                    value={gen.count ?? 1}
                    onChange={(e) =>
                      patch({
                        gen: {
                          ...gen,
                          count: Math.max(1, Math.min(8, Number(e.target.value) || 1)),
                        },
                      })
                    }
                    aria-label="Image count"
                  />
                </>
              )}
              {gen.kind === 'video' && (
                <select
                  className="nodrag rounded-full border border-canvas-line bg-transparent px-2.5 py-0.5 text-xs text-canvas-text outline-none focus:ring-1 focus:ring-canvas-strong/40"
                  value={gen.aspect ?? '16:9'}
                  onChange={(e) => patch({ gen: { ...gen, aspect: e.target.value } })}
                  aria-label="Video aspect"
                >
                  {RATIO_OPTIONS.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}
          {run_error && (
            <span
              className="ml-2 truncate text-rose-600 dark:text-rose-400"
              title={run_error}
            >
              {run_error}
            </span>
          )}
        </div>

        {/* Ref chips: show attached resources below the textarea */}
        {(resource_refs as PromptResourceRef[]).length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1" data-testid="prompt-ref-chips">
            {(resource_refs as PromptResourceRef[]).map((ref) => (
              <span
                key={ref.resource_id}
                className="inline-flex items-center gap-1 rounded-full bg-indigo-900/40 px-2 py-0.5 text-[10px] text-indigo-200"
                data-testid="prompt-ref-chip"
              >
                @{ref.name}
                <button
                  className="ml-0.5 text-indigo-400 hover:text-indigo-200"
                  aria-label={`Remove reference to ${ref.name}`}
                  onMouseDown={(e) => {
                    // prevent blur from firing before the click is processed
                    e.preventDefault();
                    const current = resource_refs as PromptResourceRef[];
                    patch({
                      resource_refs: current.filter(
                        (r) => r.resource_id !== ref.resource_id,
                      ),
                    });
                  }}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
      </div>
      <Handle
        type="source"
        position={Position.Right}
      />
    </div>
  );
}
