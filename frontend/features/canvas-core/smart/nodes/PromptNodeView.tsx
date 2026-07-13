import { useCallback, useEffect, useState } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { PromptGenSettings, PromptNodeData, PromptResourceRef } from '../types';
import { RUN_STATUS_TONE, SMART_NODE_DEFAULT_WIDTH } from '../types';
import { useGenerationModels } from './useGenerationModels';
import { useTextModels } from './useTextModels';
import { useAgents } from './useAgents';
import { useNodeDataPatch } from './useNodeDataPatch';
import { rerunPrompt } from '../regenerate';
import { RunStatusBadge } from './RunStatusBadge';
import {
  elapsedSeconds,
  formatElapsed,
  useElapsedSeconds,
} from '../../classic/nodes/elapsed';
import { useCanvasMentionPicker } from './useCanvasMentionPicker';
import { CanvasMentionPicker } from './CanvasMentionPicker';
import { useResourceSearch } from '../../../../hooks/useResourceSearch';
import type { ResourceSearchResult } from '../../../../types';
import { ASPECT_RATIOS } from '../aspectPresets';

type ActiveKind = '' | 'video' | 'image' | 'doc' | 'audio' | 'pdf';

export function PromptNodeView({ id, data, selected }: NodeProps) {
  const {
    body,
    provider_slug,
    agent_id = null,
    run_status,
    run_error,
    run_started_at = null,
    run_finished_at = null,
    resource_refs = [],   // default [] for nodes persisted before this field
    gen = null,           // absent = legacy text prompt
  } = data as unknown as PromptNodeData;
  const patch = useNodeDataPatch(id);
  const genKind = gen?.kind ?? 'text';
  const genModels = useGenerationModels(gen ? gen.kind : undefined);
  // Text-mode model options come from the platform DB catalog (same source as
  // genModels), never a hardcoded list (P0-1).
  const textModels = useTextModels();
  // Agent picker (CC3) — writes the pre-plumbed agent_id channel; the run
  // injects the agent's IDENTITY/SOUL server-side.
  const agents = useAgents();

  // Kind filter for the @-mention picker tabs (All / Video / Image / Doc …)
  const [activeKind, setActiveKind] = useState<ActiveKind>('');

  // Smart nodes keep the tone's border colour but drop the whole-card
  // animate-pulse — the status badge's dot carries the motion (P1-5;
  // classic mode still uses the full tone string).
  const haloTone = RUN_STATUS_TONE[run_status].replace('animate-pulse', '').trim();

  // Run-time pill (P1-2, Infinite's .run-time-pill): live seconds while
  // running, final duration pinned in green once succeeded. The elapsed
  // helpers are mode-agnostic despite living under classic/ (Phase 5a B4).
  const running = run_status === 'running';
  const liveElapsed = useElapsedSeconds(running ? run_started_at : null, running);
  const finalElapsed =
    run_status === 'succeeded' && run_started_at && run_finished_at
      ? elapsedSeconds(run_started_at, new Date(run_finished_at).getTime())
      : null;
  const pillText = running
    ? formatElapsed(liveElapsed)
    : finalElapsed !== null
      ? formatElapsed(finalElapsed)
      : null;

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
          {pillText && (
            <span
              data-testid="prompt-elapsed"
              className={`rounded-full px-1.5 py-0.5 font-mono text-[10px] tabular-nums ${
                running
                  ? 'mh-accent-chip'
                  : 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
              }`}
            >
              {pillText}
            </span>
          )}
          <RunStatusBadge status={run_status} />
        </div>
      </div>
      {/* relative so the CanvasMentionPicker's `bottom-full` positions above this section */}
      <div className="relative p-3">
        <textarea
          // nodrag → React Flow doesn't start a drag from this input
          // nowheel → wheel events scroll the textarea instead of zooming canvas
          className="nodrag nowheel min-h-[3.5rem] w-full resize-y bg-transparent text-[13px] text-ink-200 outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40"
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
            <div className="flex flex-1 items-center gap-1.5">
              <select
                className="nodrag min-w-0 flex-1 truncate rounded-full border border-canvas-line bg-transparent px-2.5 py-0.5 text-xs text-canvas-text outline-none focus:ring-1 focus:ring-canvas-strong/40"
                value={provider_slug}
                onChange={(e) => patch({ provider_slug: e.target.value })}
                aria-label="Prompt provider"
              >
                {/* Empty value → backend resolves the DB catalog default. */}
                <option value="">Catalog default</option>
                {textModels.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.display_name || m.name}
                  </option>
                ))}
              </select>
              <select
                className="nodrag min-w-0 flex-1 truncate rounded-full border border-canvas-line bg-transparent px-2.5 py-0.5 text-xs text-canvas-text outline-none focus:ring-1 focus:ring-canvas-strong/40"
                value={agent_id ?? ''}
                onChange={(e) => patch({ agent_id: e.target.value || null })}
                aria-label="Prompt agent"
              >
                {/* Empty → plain runner (no persona injected). */}
                <option value="">No agent</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>
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
                    {ASPECT_RATIOS.map((r) => (
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
                  {ASPECT_RATIOS.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}
        </div>

        {/* Ref chips: show attached resources below the textarea */}
        {(resource_refs as PromptResourceRef[]).length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1" data-testid="prompt-ref-chips">
            {(resource_refs as PromptResourceRef[]).map((ref) => (
              <span
                key={ref.resource_id}
                className="mh-accent-chip inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px]"
                data-testid="prompt-ref-chip"
              >
                @{ref.name}
                <button
                  className="ml-0.5 opacity-70 hover:opacity-100"
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

        {/* Failed-run recovery panel (P3-B): a full-width rose panel with the
            error text + Retry, replacing the old truncated one-liner + a tiny
            header chip. Mirrors the OutputNodeView RecoverCell shape. */}
        {run_status === 'failed' && (
          <div
            data-testid="prompt-failure-panel"
            role="alert"
            className="mt-2 rounded-lg border border-rose-400/50 bg-rose-400/10 p-2"
          >
            <div className="text-[11px] font-bold text-rose-500">Run failed</div>
            {run_error && (
              <div className="mt-0.5 text-[10px] text-canvas-muted" title={run_error}>
                {run_error}
              </div>
            )}
            <button
              type="button"
              onClick={() => void rerunPrompt(id)}
              className="nodrag mh-chip mt-1.5 !border-rose-400/60 !text-rose-500"
            >
              Retry
            </button>
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
