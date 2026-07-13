/**
 * LlmNodeView — the Standard canvas's standalone LLM card (IC 普通画布's
 * LLM node, dual-canvas Phase 2.1).
 *
 * INPUT: typed text, or wired prompt/llm upstreams (their text is
 * prepended at run time). Run calls the same backend prompt route the
 * composer uses (POST /canvases/runs/prompts). OUTPUT: the response text
 * rendered in-node with a copy affordance — this node never spawns
 * output nodes; wire it into a prompt to feed generation.
 */

import { useCallback, useState } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Copy, Play } from 'lucide-react';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { createBackendRunner } from '../runner.backend';
import type { LlmNodeData, PromptNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
import { RunStatusBadge } from './RunStatusBadge';
import { useAgents } from './useAgents';
import { useNodeDataPatch } from './useNodeDataPatch';
import { useTextModels } from './useTextModels';

const asObj = (n: unknown) => n as Record<string, unknown>;

/** Text from wired upstream prompt/llm nodes, in connection order. */
export function upstreamTextFor(
  llmId: string,
  nodes: unknown[],
  connections: Array<{ source?: unknown; target?: unknown }>,
): string[] {
  const byId = new Map(nodes.map((n) => [String(asObj(n).id), n]));
  const texts: string[] = [];
  for (const c of connections) {
    if (String(c.target) !== llmId) continue;
    const upstream = byId.get(String(c.source));
    if (!upstream) continue;
    const type = asObj(upstream).type;
    const data = (asObj(upstream).data ?? {}) as Record<string, unknown>;
    const text =
      type === 'prompt'
        ? (data as unknown as PromptNodeData).body
        : type === 'llm'
          ? (data as unknown as LlmNodeData).output_text
          : '';
    if (typeof text === 'string' && text.trim()) texts.push(text.trim());
  }
  return texts;
}

export function LlmNodeView({ id, data, selected }: NodeProps) {
  const d = data as unknown as LlmNodeData;
  const patch = useNodeDataPatch(id);
  const textModels = useTextModels();
  const agents = useAgents();
  const [copied, setCopied] = useState(false);

  const run = useCallback(async () => {
    const store = useCanvasCoreStore.getState();
    const { canvasId, nodes, connections } = store;
    const current = ((nodes.find((n) => asObj(n).id === id) as
      | { data?: LlmNodeData }
      | undefined)?.data ?? d) as LlmNodeData;
    const parts = [
      ...upstreamTextFor(id, nodes, connections as never),
      current.input_text.trim(),
    ].filter(Boolean);
    if (parts.length === 0 || !canvasId) return;
    patch({ run_status: 'running', run_error: null });
    const caller = createBackendRunner({ canvasId });
    const result = await caller({
      promptId: id,
      body: parts.join('\n\n'),
      provider_slug: current.provider_slug,
      agent_id: current.agent_id,
    });
    if (result.ok) {
      patch({ run_status: 'succeeded', output_text: result.text, run_error: null });
    } else {
      patch({ run_status: 'failed', run_error: result.error ?? 'run failed' });
    }
  }, [d, id, patch]);

  const copyOutput = useCallback(() => {
    if (!d.output_text) return;
    void navigator.clipboard?.writeText(d.output_text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    });
  }, [d.output_text]);

  return (
    <div
      data-testid="smart-llm-node"
      className={`mh-node border-canvas-line ${selected ? 'mh-node-selected' : ''}`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.llm }}
    >
      <div className="mh-node-head">
        <div className="mh-node-title">LLM</div>
        <RunStatusBadge status={d.run_status} />
      </div>
      <div className="p-3">
        <div className="flex items-center gap-1.5">
          <select
            className="nodrag min-w-0 flex-1 truncate rounded-full border border-canvas-line bg-transparent px-2.5 py-0.5 text-xs text-canvas-text outline-none focus:ring-1 focus:ring-canvas-strong/40"
            value={d.provider_slug}
            onChange={(e) => patch({ provider_slug: e.target.value })}
            aria-label="LLM provider"
          >
            <option value="">Catalog default</option>
            {textModels.map((m) => (
              <option key={m.name} value={m.name}>
                {m.display_name || m.name}
              </option>
            ))}
          </select>
          <select
            className="nodrag min-w-0 flex-1 truncate rounded-full border border-canvas-line bg-transparent px-2.5 py-0.5 text-xs text-canvas-text outline-none focus:ring-1 focus:ring-canvas-strong/40"
            value={d.agent_id ?? ''}
            onChange={(e) => patch({ agent_id: e.target.value || null })}
            aria-label="LLM agent"
          >
            <option value="">No agent</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </div>

        <div className="mt-2 text-[10px] font-semibold uppercase tracking-wide text-canvas-muted">
          Input
        </div>
        <textarea
          className="nodrag nowheel mt-1 w-full resize-none rounded-lg border border-canvas-line bg-transparent p-2 text-xs text-canvas-text outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40"
          rows={3}
          placeholder="Type here, or wire in a Prompt node…"
          value={d.input_text}
          onChange={(e) => patch({ input_text: e.target.value })}
          aria-label="LLM input"
        />

        <button
          type="button"
          onClick={() => void run()}
          disabled={d.run_status === 'running'}
          className="nodrag mt-2 flex items-center gap-1.5 rounded-full border border-canvas-line px-3 py-1 text-xs font-medium text-canvas-text transition-colors hover:border-indigo-500/50 hover:text-indigo-400 disabled:opacity-50"
        >
          <Play size={11} />
          Run
        </button>

        <div className="mt-2 flex items-center justify-between">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-canvas-muted">
            Output
          </span>
          {d.output_text && (
            <button
              type="button"
              onClick={copyOutput}
              aria-label="Copy output"
              className="nodrag flex items-center gap-1 text-[10px] text-canvas-muted transition-colors hover:text-canvas-text"
            >
              <Copy size={10} />
              {copied ? 'Copied' : 'Copy'}
            </button>
          )}
        </div>
        <div
          data-testid="llm-output"
          className="nowheel mt-1 max-h-40 min-h-[56px] overflow-y-auto whitespace-pre-wrap rounded-lg border border-canvas-line/60 bg-canvas-card/40 p-2 text-xs text-canvas-text"
        >
          {d.output_text || (
            <span className="italic text-canvas-muted">
              Run to produce text — wire it into a Prompt to feed generation
            </span>
          )}
        </div>
        {d.run_error && (
          <div className="mt-1.5 text-[11px] text-rose-400">{d.run_error}</div>
        )}
      </div>
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
