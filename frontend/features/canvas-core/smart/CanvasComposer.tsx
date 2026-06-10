/**
 * Smart-mode composer (Phase 2 of canvas + AI upgrade).
 *
 * Bottom toolbar:
 *   + Shot / + Prompt / + Output / + Loop
 *   Run                — execute selected prompts only
 *   Cascade Run        — execute every prompt in topo order
 *
 * Run uses the mock runner from `./runner` — the real provider adapter
 * (Jimeng CLI / nous-center) lands in the next slice; the lifecycle
 * UX is fully wired here so the swap is mechanical.
 *
 * New nodes drop at the visual centre of the surface (screenToWorld
 * via the viewport math).
 */

import { useCallback, useMemo, useState } from 'react';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { screenToWorld } from '../utils/viewport';
import {
  createLoopNode,
  createOutputNode,
  createPromptNode,
  createShotNode,
} from './factories';
import { createBackendRunner } from './runner.backend';
import {
  mockRunner,
  runPrompts,
  type PromptCaller,
  type RunHandlers,
  type RunnerContext,
} from './runner';
import { topoSortPrompts } from './topology';
import type { PromptNodeData } from './types';

interface CanvasComposerOptions {
  /** When passed, new nodes are positioned at the centre of this DOM
   *  rect (translated into world coords). Falls back to viewport
   *  origin when not provided — useful for tests / headless renders. */
  surfaceRef?: React.RefObject<HTMLElement | null>;
  /** Override the runner — tests pass a deterministic one. Default at
   *  runtime is the backend runner bound to the open canvas; if no
   *  canvas is loaded yet, falls back to the mock so the UX is still
   *  exercisable in isolation. */
  runner?: PromptCaller;
}

export function CanvasComposer({
  surfaceRef,
  runner: runnerOverride,
}: CanvasComposerOptions = {}) {
  const viewport = useCanvasCoreStore((s) => s.viewport);
  const nodes = useCanvasCoreStore((s) => s.nodes);
  const connections = useCanvasCoreStore((s) => s.connections);
  const selection = useCanvasCoreStore((s) => s.selection);
  const canvasId = useCanvasCoreStore((s) => s.canvasId);
  const setNodes = useCanvasCoreStore((s) => s.setNodes);
  const setSelection = useCanvasCoreStore((s) => s.setSelection);
  const patchNode = useCanvasCoreStore((s) => s.patchNode);

  const [running, setRunning] = useState(false);

  const runner = useMemo<PromptCaller>(() => {
    if (runnerOverride) return runnerOverride;
    if (canvasId) return createBackendRunner({ canvasId });
    return mockRunner;
  }, [runnerOverride, canvasId]);

  const dropPosition = useCallback((): { x: number; y: number } => {
    const rect = surfaceRef?.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    const screenCenter = {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
    return screenToWorld(screenCenter, viewport);
  }, [surfaceRef, viewport]);

  const addNode = useCallback(
    (kind: 'shot' | 'prompt' | 'output' | 'loop') => {
      const position = dropPosition();
      const node =
        kind === 'shot'
          ? createShotNode({}, { position })
          : kind === 'prompt'
            ? createPromptNode({}, { position })
            : kind === 'output'
              ? createOutputNode({}, { position })
              : createLoopNode({}, { position });
      setNodes([...nodes, node]);
      setSelection([node.id]);
    },
    [dropPosition, nodes, setNodes, setSelection],
  );

  const buildContexts = useCallback(
    (ids: string[]): RunnerContext[] => {
      const byId = new Map(
        nodes.map((n) => [(n as Record<string, unknown>).id as string, n]),
      );
      return ids
        .map((id) => {
          const n = byId.get(id);
          if (!n) return null;
          const data = (n as Record<string, unknown>).data as
            | PromptNodeData
            | undefined;
          if (!data) return null;
          return {
            promptId: id,
            body: data.body,
            provider_slug: data.provider_slug,
            agent_id: data.agent_id,
          };
        })
        .filter((v): v is RunnerContext => v !== null);
    },
    [nodes],
  );

  const handlers: RunHandlers = {
    onStatusChange: (id, status, fields) => {
      patchNode(id, { data: { run_status: status, ...fields } });
    },
  };

  const doRunIds = useCallback(
    async (ids: string[]) => {
      if (ids.length === 0 || running) return;
      setRunning(true);
      try {
        await runPrompts(buildContexts(ids), runner, handlers);
      } finally {
        setRunning(false);
      }
    },
    [buildContexts, handlers, runner, running],
  );

  const onRunSelected = useCallback(() => {
    const promptIds = nodes
      .filter((n) => (n as Record<string, unknown>).type === 'prompt')
      .map((n) => (n as Record<string, unknown>).id as string)
      .filter((id) => selection.includes(id));
    void doRunIds(promptIds);
  }, [nodes, selection, doRunIds]);

  const onCascadeRun = useCallback(() => {
    const { order } = topoSortPrompts(nodes, connections);
    void doRunIds(order);
  }, [nodes, connections, doRunIds]);

  return (
    <div
      role="toolbar"
      aria-label="Smart canvas composer"
      className="pointer-events-auto absolute inset-x-0 bottom-4 mx-auto flex w-fit gap-1 rounded-md border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-900"
    >
      <ComposerButton onClick={() => addNode('shot')}>+ Shot</ComposerButton>
      <ComposerButton onClick={() => addNode('prompt')}>+ Prompt</ComposerButton>
      <ComposerButton onClick={() => addNode('output')}>+ Output</ComposerButton>
      <ComposerButton onClick={() => addNode('loop')}>+ Loop</ComposerButton>
      <Divider />
      <ComposerButton
        onClick={onRunSelected}
        disabled={running || selection.length === 0}
      >
        {running ? 'Running…' : 'Run'}
      </ComposerButton>
      <ComposerButton
        onClick={onCascadeRun}
        disabled={running}
        emphasis="primary"
      >
        {running ? 'Running…' : 'Cascade Run'}
      </ComposerButton>
    </div>
  );
}

function Divider() {
  return (
    <div className="mx-1 my-1 w-px bg-slate-200 dark:bg-slate-700" />
  );
}

function ComposerButton({
  onClick,
  children,
  disabled,
  emphasis,
}: {
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
  emphasis?: 'primary';
}) {
  const primary =
    'bg-indigo-600 text-white hover:bg-indigo-700 disabled:bg-indigo-300';
  const neutral =
    'text-slate-700 hover:bg-slate-100 disabled:text-slate-300 dark:text-slate-200 dark:hover:bg-slate-800 dark:disabled:text-slate-600';
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`rounded px-3 py-1.5 text-sm font-medium ${
        emphasis === 'primary' ? primary : neutral
      } disabled:cursor-not-allowed`}
    >
      {children}
    </button>
  );
}
