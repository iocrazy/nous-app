import { useCallback, useEffect, useState } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { PromptNodeData, PromptResourceRef } from '../types';
import { RUN_STATUS_TONE, SMART_NODE_DEFAULT_WIDTH } from '../types';
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

export function PromptNodeView({ id, data, selected }: NodeProps) {
  const {
    body,
    provider_slug,
    run_status,
    run_error,
    resource_refs = [],   // default [] for nodes persisted before this field
  } = data as unknown as PromptNodeData;
  const patch = useNodeDataPatch(id);

  // Kind filter for the @-mention picker tabs (All / Video / Image / Doc …)
  const [activeKind, setActiveKind] = useState<ActiveKind>('');

  const haloTone = selected ? 'border-indigo-500' : RUN_STATUS_TONE[run_status];

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
      className={`rounded-md border-2 bg-white shadow dark:bg-ink-900 ${haloTone}`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.prompt }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2 !w-2 !bg-ink-400"
      />
      <div className="flex items-center justify-between border-b border-ink-200 px-3 py-1.5 dark:border-ink-700">
        <div className="text-xs font-semibold uppercase tracking-wide text-ink-500 dark:text-ink-400">
          Prompt
        </div>
        <div className="text-[10px] uppercase tracking-wider text-ink-400">
          {run_status}
        </div>
      </div>
      {/* relative so the CanvasMentionPicker's `bottom-full` positions above this section */}
      <div className="relative p-3">
        <textarea
          // nodrag → React Flow doesn't start a drag from this input
          // nowheel → wheel events scroll the textarea instead of zooming canvas
          className="nodrag nowheel min-h-[3.5rem] w-full resize-y bg-transparent text-sm text-ink-800 outline-none placeholder:text-ink-400 focus:ring-1 focus:ring-indigo-300 dark:text-ink-200"
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
          <select
            className="nodrag flex-1 truncate rounded border border-ink-200 bg-transparent px-1 py-0.5 text-xs text-ink-700 outline-none focus:ring-1 focus:ring-indigo-300 dark:border-ink-700 dark:text-ink-200"
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
        className="!h-2 !w-2 !bg-ink-400"
      />
    </div>
  );
}
