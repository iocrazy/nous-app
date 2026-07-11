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

import { useCallback, useMemo, useRef, useState } from 'react';

import { arrangeLayout } from '../../../canvas-kit/arrangeLayout';
import { useKnifeStore } from '../../../canvas-kit/knifeStore';
import { prepareDuplicate } from '../store/clipboard';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { screenToWorld } from '../utils/viewport';
import {
  createLoopNode,
  createOutputNode,
  createPromptNode,
  createShotNode,
} from './factories';
import { upsertGenerationSlots } from './genSlots';
import { resolveSourceUrl } from './promptInputs';
import { withGenerationRunner } from './generationRunner';
import { createBackendRunner } from './runner.backend';
import {
  mockRunner,
  runPrompts,
  type PromptCaller,
  type RunHandlers,
  type RunnerContext,
} from './runner';
import { topoSortPrompts } from './topology';
import type { OutputKind, PromptNodeData } from './types';
import { SMART_NODE_TYPES } from './nodes/registry';
import { parseWorkflow, serializeWorkflow, workflowFilename } from './workflowIO';

const SMART_NODE_TYPE_KEYS = new Set(Object.keys(SMART_NODE_TYPES));
/** Refuse absurd files before reading them into memory. */
const MAX_WORKFLOW_FILE_BYTES = 5 * 1024 * 1024;

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
  const knifeActive = useKnifeStore((s) => s.active);
  const toggleKnife = useKnifeStore((s) => s.toggle);

  const runner = useMemo<PromptCaller>(() => {
    const base = runnerOverride
      ? runnerOverride
      : canvasId
        ? createBackendRunner({ canvasId })
        : mockRunner;
    // Image/video prompts route through the G4-B1 generation tasks; text
    // prompts pass straight through to the base caller. onPhase surfaces
    // the engine queue (jimeng) as a 'queued' halo mid-poll (G4-F3).
    return withGenerationRunner(base, {
      canvasId,
      onPhase: (id, phase) => {
        useCanvasCoreStore.getState().patchNode(id, {
          data: { run_status: phase === 'queued' ? 'queued' : 'running' },
        });
      },
    });
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
        .map((id): RunnerContext | null => {
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
            gen: data.gen ?? null,
            source_url: resolveSourceUrl(id, nodes, connections),
          };
        })
        .filter((v): v is RunnerContext => v !== null);
    },
    [nodes, connections],
  );

  const handlers: RunHandlers = {
    onStatusChange: (id, status, fields) => {
      patchNode(id, { data: { run_status: status, ...fields } });
    },
    onResult: (id, result) => {
      // Generation results land in per-prompt output slots (G4-F1) —
      // reused on re-runs; F2 upgrades to images[] + history archive.
      if (result.ok && result.urls?.length) {
        upsertGenerationSlots(
          id,
          result.urls,
          (result.media_kind as OutputKind) ?? 'image',
        );
      }
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

  const onArrange = useCallback(() => {
    // setNodes (not patchNode) so one layout pass = one undoable edit.
    setNodes(
      arrangeLayout(
        nodes as Parameters<typeof arrangeLayout>[0],
        connections as Parameters<typeof arrangeLayout>[1],
      ),
    );
  }, [nodes, connections, setNodes]);

  // ---- Workflow export / import (G5) ------------------------------------
  const importInputRef = useRef<HTMLInputElement | null>(null);
  const [workflowError, setWorkflowError] = useState<string | null>(null);

  const onExport = useCallback(() => {
    const selectedSet = new Set(selection);
    const selected = nodes.filter((n) =>
      selectedSet.has((n as Record<string, unknown>).id as string),
    );
    if (selected.length === 0) return;
    const payload = serializeWorkflow('smart', selected, connections);
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: 'application/json',
    });
    const href = URL.createObjectURL(blob);
    try {
      const link = document.createElement('a');
      link.href = href;
      link.download = workflowFilename(payload.nodes.length);
      document.body.appendChild(link);
      link.click();
      link.remove();
    } finally {
      URL.revokeObjectURL(href);
    }
  }, [nodes, connections, selection]);

  const onImportFile = useCallback(
    async (file: File) => {
      setWorkflowError(null);
      try {
        if (file.size > MAX_WORKFLOW_FILE_BYTES) {
          throw new Error('Workflow file is too large (5 MB limit)');
        }
        const store = useCanvasCoreStore.getState();
        const payload = parseWorkflow(await file.text(), {
          allowedTypes: SMART_NODE_TYPE_KEYS,
          expectedKind: store.kind,
        });
        // Re-id + tag hygiene through the same machinery as paste/duplicate.
        const existing = new Set(
          store.nodes.map((n) => (n as Record<string, unknown>).id as string),
        );
        const prepared = prepareDuplicate(
          payload.nodes,
          payload.connections,
          existing,
        );
        if (!prepared) return;
        store.setNodes([...store.nodes, ...prepared.nodes]);
        if (prepared.connections.length > 0) {
          store.setConnections([...store.connections, ...prepared.connections]);
        }
        store.setSelection(
          prepared.nodes.map((n) => (n as Record<string, unknown>).id as string),
        );
      } catch (err) {
        setWorkflowError(
          err instanceof Error ? err.message : 'Failed to import workflow',
        );
      }
    },
    [],
  );

  return (
    <div
      role="toolbar"
      aria-label="Smart canvas composer"
      className="canvas-island pointer-events-auto absolute inset-x-0 bottom-4 mx-auto flex w-fit gap-1 p-1.5"
    >
      <ComposerButton onClick={() => addNode('shot')}>+ Shot</ComposerButton>
      <ComposerButton onClick={() => addNode('prompt')}>+ Prompt</ComposerButton>
      <ComposerButton onClick={() => addNode('output')}>+ Output</ComposerButton>
      <ComposerButton onClick={() => addNode('loop')}>+ Loop</ComposerButton>
      <Divider />
      <ComposerButton onClick={onArrange} disabled={nodes.length === 0}>
        Arrange
      </ComposerButton>
      <ComposerButton onClick={toggleKnife} emphasis={knifeActive ? 'primary' : undefined}>
        {knifeActive ? 'Knife ✕' : 'Knife'}
      </ComposerButton>
      <ComposerButton onClick={onExport} disabled={selection.length === 0}>
        Export
      </ComposerButton>
      <ComposerButton onClick={() => importInputRef.current?.click()}>
        Import
      </ComposerButton>
      <input
        ref={importInputRef}
        data-testid="workflow-import-input"
        type="file"
        accept="application/json,.json"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = ''; // allow re-importing the same file
          if (file) void onImportFile(file);
        }}
      />
      {workflowError && (
        <div
          role="alert"
          className="absolute bottom-full left-1/2 mb-2 -translate-x-1/2 whitespace-nowrap rounded bg-rose-600 px-3 py-1.5 text-xs font-medium text-white shadow-lg"
        >
          {workflowError}
        </div>
      )}
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
  return <div className="mx-1 my-1 w-px bg-canvas-line" />;
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
    'bg-canvas-strong text-canvas-card hover:opacity-90 disabled:opacity-40';
  const neutral =
    'text-canvas-text hover:bg-canvas-line/40 disabled:text-canvas-muted/50';
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
