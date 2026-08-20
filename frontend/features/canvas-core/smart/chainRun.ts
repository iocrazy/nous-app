// features/canvas-core/smart/chainRun.ts
//
// IC "一键运行" (Run chain, smart-canvas.js canRunSmartCascade /
// syncCascadeRunButton): the tail prompt of a cascade gets a second run
// button that executes the WHOLE upstream chain in topological order —
// upstream outputs feed downstream prompts exactly as individual runs do.
// Stop is cooperative (checked between prompts, running prompt finishes).

import { create } from 'zustand';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { withGenerationRunner } from './generationRunner';
import { resolveEntityRef } from './entityRef';
import {
  resolveEffectiveSourceUrl,
  resolveEffectiveSourceUrls,
} from './promptInputs';
import { createBackendRunner } from './runner.backend';
import {
  mockRunner,
  runPrompts,
  type PromptCaller,
  type RunnerContext,
} from './runner';
import { topoSortPrompts } from './topology';
import type { PromptNodeData } from './types';

const asObj = (n: unknown) => n as Record<string, unknown>;
const idOf = (n: unknown): string | null => {
  const v = asObj(n).id;
  return typeof v === 'string' ? v : null;
};
const typeOf = (n: unknown): string =>
  typeof asObj(n).type === 'string' ? (asObj(n).type as string) : '';

interface ChainRunState {
  runningTail: string | null;
  stopRequested: boolean;
  /** Test seam — replaces the backend caller. */
  runnerOverride: PromptCaller | null;
  start(tailId: string): void;
  finish(): void;
  requestStop(): void;
  setRunnerOverride(caller: PromptCaller | null): void;
}

export const useChainRunStore = create<ChainRunState>((set) => ({
  runningTail: null,
  stopRequested: false,
  runnerOverride: null,
  start: (tailId) => set({ runningTail: tailId, stopRequested: false }),
  finish: () => set({ runningTail: null, stopRequested: false }),
  requestStop: () => set({ stopRequested: true }),
  setRunnerOverride: (caller) => set({ runnerOverride: caller }),
}));

function edgesOf(connections: unknown[]): { source: string; target: string }[] {
  const out: { source: string; target: string }[] = [];
  for (const c of connections) {
    const o = asObj(c);
    if (typeof o.source === 'string' && typeof o.target === 'string') {
      out.push({ source: o.source, target: o.target });
    }
  }
  return out;
}

/** Ancestor prompt ids of `tailId` (walking connections backwards through
 *  intermediate nodes) plus the tail itself, in topological run order. */
export function upstreamChain(
  tailId: string,
  nodes: unknown[],
  connections: unknown[],
): string[] {
  const promptIds = new Set(
    nodes.filter((n) => typeOf(n) === 'prompt').map(idOf).filter(Boolean) as string[],
  );
  const backward = new Map<string, string[]>();
  for (const e of edgesOf(connections)) {
    if (!backward.has(e.target)) backward.set(e.target, []);
    backward.get(e.target)!.push(e.source);
  }
  const keep = new Set<string>([tailId]);
  const seen = new Set<string>();
  const walk = (id: string) => {
    if (seen.has(id)) return;
    seen.add(id);
    for (const s of backward.get(id) ?? []) {
      if (promptIds.has(s)) keep.add(s);
      walk(s);
    }
  };
  walk(tailId);
  const { order } = topoSortPrompts(nodes as never, connections as never, {
    promptIdAllowlist: keep,
  });
  return order;
}

/** IC canRunSmartCascade: a chain tail has upstream prompts and no
 *  downstream ones — that's where the Run-chain button lives. */
export function isChainTail(
  nodeId: string,
  nodes: unknown[],
  connections: unknown[],
): boolean {
  const chain = upstreamChain(nodeId, nodes, connections);
  if (chain.length < 2) return false;
  const promptIds = new Set(
    nodes.filter((n) => typeOf(n) === 'prompt').map(idOf).filter(Boolean) as string[],
  );
  const forward = new Map<string, string[]>();
  for (const e of edgesOf(connections)) {
    if (!forward.has(e.source)) forward.set(e.source, []);
    forward.get(e.source)!.push(e.target);
  }
  const seen = new Set<string>();
  let hasDownstreamPrompt = false;
  const walk = (id: string) => {
    if (seen.has(id) || hasDownstreamPrompt) return;
    seen.add(id);
    for (const t of forward.get(id) ?? []) {
      if (promptIds.has(t)) {
        hasDownstreamPrompt = true;
        return;
      }
      walk(t);
    }
  };
  walk(nodeId);
  return !hasDownstreamPrompt;
}

function resolveCaller(): PromptCaller {
  const override = useChainRunStore.getState().runnerOverride;
  if (override) return override;
  const canvasId = useCanvasCoreStore.getState().canvasId;
  const base = canvasId ? createBackendRunner({ canvasId }) : mockRunner;
  return withGenerationRunner(base, {
    canvasId,
    shouldStop: () => useChainRunStore.getState().stopRequested,
  });
}

export async function startChainRun(tailId: string): Promise<boolean> {
  const runStore = useChainRunStore.getState();
  if (runStore.runningTail) return false;
  runStore.start(tailId);
  try {
    const { nodes, connections, patchNode, canvasId } =
      useCanvasCoreStore.getState();
    const startCanvasId = canvasId;
    const sameCanvas = () =>
      useCanvasCoreStore.getState().canvasId === startCanvasId;

    const contexts: RunnerContext[] = [];
    for (const pid of upstreamChain(tailId, nodes, connections)) {
      // Contexts resolve LAZILY at dispatch time via a getter chain would be
      // ideal, but source urls come from the store which updates as each
      // upstream run lands — so resolve per prompt just before dispatch.
      contexts.push({ promptId: pid } as RunnerContext);
    }

    const results = await runPrompts(
      contexts,
      async (thin) => {
        // Late-bind the full context: upstream outputs written by earlier
        // prompts in this chain must be visible to later ones.
        const live = useCanvasCoreStore.getState();
        const prompt = live.nodes.find((n) => idOf(n) === thin.promptId);
        const data = asObj(prompt ?? {}).data as PromptNodeData | undefined;
        if (!prompt || !data)
          return { ok: false, text: '', error: 'prompt missing' };
        const ctx: RunnerContext = {
          promptId: thin.promptId,
          body: data.body,
          provider_slug: data.provider_slug,
          agent_id: data.agent_id,
          gen: data.gen ?? null,
          source_url: resolveEffectiveSourceUrl(prompt, live.nodes, live.connections),
          source_urls: resolveEffectiveSourceUrls(prompt, live.nodes, live.connections),
          entity_ref: resolveEntityRef(thin.promptId, live.nodes, live.connections),
        };
        return resolveCaller()(ctx);
      },
      {
        onStatusChange: (id, status, fields) => {
          if (!sameCanvas()) return;
          patchNode(id, { data: { run_status: status, ...fields } });
        },
      },
      {
        shouldStop: () =>
          useChainRunStore.getState().stopRequested || !sameCanvas(),
      },
    );
    return results.length > 0 && results.every((r) => r.ok);
  } finally {
    useChainRunStore.getState().finish();
  }
}
