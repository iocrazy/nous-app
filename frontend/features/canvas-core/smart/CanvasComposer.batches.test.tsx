// features/canvas-core/smart/CanvasComposer.batches.test.tsx
// Per-node run locking (P2-9 — Infinite's node-granular running state):
// the composer no longer freezes while a batch runs. New Runs dispatch for
// prompts that are NOT already in flight; Stop stops every active batch
// (P0-4 semantics per batch: unstarted prompts never dispatch, nodes go
// back to idle).

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { CanvasComposer } from './CanvasComposer';
import { _resetIdCounter } from './factories';
import type { PromptCaller, RunnerContext } from './runner';

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '4242',
    kind: 'smart',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-10T12:00:00+00:00',
  });
});

afterEach(() => {
  useCanvasCoreStore.getState().reset();
  _resetIdCounter();
});

const prompt = (id: string, body: string) => ({
  id,
  type: 'prompt',
  position: { x: 0, y: 0 },
  data: {
    body,
    provider_slug: '',
    agent_id: null,
    run_status: 'idle',
    run_started_at: null,
    run_finished_at: null,
    run_error: null,
    resource_refs: [],
  },
});

function seedPrompts(ids: string[]): void {
  useCanvasCoreStore.setState({
    nodes: ids.map((id) => prompt(id, `body-${id}`)),
    connections: [],
    selection: [],
  });
}

/** Runner whose promises we resolve by hand, per prompt id. */
function deferredRunner() {
  const pending = new Map<string, (v: { ok: boolean; text: string; error: null }) => void>();
  const calls: string[] = [];
  const caller: PromptCaller = (ctx: RunnerContext) => {
    calls.push(ctx.promptId);
    return new Promise((res) => pending.set(ctx.promptId, res));
  };
  const settle = (id: string) =>
    act(() => {
      pending.get(id)?.({ ok: true, text: 'done', error: null });
      pending.delete(id);
    });
  return { caller, calls, settle };
}

async function runSelected(ids: string[]): Promise<void> {
  act(() => useCanvasCoreStore.setState({ selection: ids }));
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Run' }));
  });
}

describe('CanvasComposer — per-node batches (P2-9)', () => {
  it('a second Run dispatches while the first batch is still in flight', async () => {
    seedPrompts(['pA', 'pB']);
    const { caller, calls, settle } = deferredRunner();
    render(<CanvasComposer runner={caller} />);

    await runSelected(['pA']);
    expect(calls).toEqual(['pA']);

    // Old behaviour froze here (running=true → early return).
    await runSelected(['pB']);
    expect(calls).toEqual(['pA', 'pB']);

    settle('pA');
    settle('pB');
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /Stop/ })).toBeNull(),
    );
  });

  it('a prompt already in flight is filtered from new Runs', async () => {
    seedPrompts(['pA']);
    const { caller, calls, settle } = deferredRunner();
    render(<CanvasComposer runner={caller} />);

    await runSelected(['pA']);
    await runSelected(['pA']); // double dispatch attempt
    expect(calls).toEqual(['pA']);
    settle('pA');
  });

  it('nodes marked queued/running in the store (loop/rerun) are filtered too', async () => {
    seedPrompts(['pA', 'pB']);
    act(() => {
      useCanvasCoreStore.getState().patchNode('pA', { data: { run_status: 'running' } });
    });
    const { caller, calls, settle } = deferredRunner();
    render(<CanvasComposer runner={caller} />);

    await runSelected(['pA', 'pB']);
    expect(calls).toEqual(['pB']);
    settle('pB');
  });

  it('Run / Cascade Run stay visible while a batch runs; Stop appears beside them', async () => {
    seedPrompts(['pA', 'pB']);
    const { caller, settle } = deferredRunner();
    render(<CanvasComposer runner={caller} />);

    await runSelected(['pA']);
    expect(screen.getByRole('button', { name: 'Run' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Cascade Run' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Stop' })).toBeTruthy();
    settle('pA');
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /Stop/ })).toBeNull(),
    );
  });

  it('Stop stops EVERY active batch: undispatched prompts stay untouched (P0-4)', async () => {
    seedPrompts(['pA', 'pB', 'pC']);
    const { caller, calls, settle } = deferredRunner();
    render(<CanvasComposer runner={caller} />);

    // Batch 1: pA then pB (sequential — pB not yet dispatched).
    await runSelected(['pA', 'pB']);
    // Batch 2: pC.
    await runSelected(['pC']);
    expect(calls).toEqual(['pA', 'pC']);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    });
    expect(screen.getByRole('button', { name: 'Stopping…' })).toBeTruthy();

    settle('pA');
    settle('pC');
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /Stop/ })).toBeNull(),
    );
    // pB was never dispatched (its batch stopped between prompts).
    expect(calls).toEqual(['pA', 'pC']);
    const pB = useCanvasCoreStore
      .getState()
      .nodes.find((n) => (n as Record<string, unknown>).id === 'pB');
    expect(
      ((pB as Record<string, unknown>).data as { run_status: string }).run_status,
    ).toBe('idle');
  });

  it('Cascade Run disables while a cascade batch runs; selected Run stays usable', async () => {
    seedPrompts(['pA', 'pB']);
    const { caller, calls, settle } = deferredRunner();
    render(<CanvasComposer runner={caller} />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Cascade Run' }));
    });
    expect(calls.length).toBe(1); // topo-sequential: first prompt in flight
    expect(
      (screen.getByRole('button', { name: 'Cascade Run' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole('button', { name: 'Run' }) as HTMLButtonElement).disabled,
    ).toBe(true); // nothing selected — normal rule, not the batch lock

    settle(calls[0]);
    await waitFor(() => expect(calls.length).toBe(2));
    settle(calls[1]);
    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Cascade Run' }) as HTMLButtonElement)
          .disabled,
      ).toBe(false),
    );
  });
});
