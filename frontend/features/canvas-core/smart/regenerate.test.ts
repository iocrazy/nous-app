// features/canvas-core/smart/regenerate.test.ts
// Regenerate from an output slot (Infinite parity G7): re-runs the slot's
// source prompt with its CURRENT body/gen settings; results land through the
// normal slot channel (upsert + history archive). Store-wired like loopRun:
// canvasId snapshot guard, runner override for tests, per-node re-entrancy.

import { afterEach, describe, expect, it } from 'vitest';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasNode } from '../types';
import { promptIdForOutput, regenerateForOutput } from './regenerate';
import { useRegenStore } from './regenStore';
import type { RunnerResult } from './runner';

const PROMPT: CanvasNode = {
  id: 'p1',
  type: 'prompt',
  position: { x: 0, y: 0 },
  data: {
    body: 'a cat',
    provider_slug: 'jimeng',
    agent_id: null,
    run_status: 'succeeded',
    resource_refs: [],
    gen: { kind: 'image', model: 'jimeng-4', aspect: '1:1', count: 1 },
  },
};

const SLOT: CanvasNode = {
  id: 'out1',
  type: 'output',
  position: { x: 320, y: 0 },
  data: {
    kind: 'image',
    preview_url: '/gm/1/cover',
    images: [{ url: '/gm/1/cover', kind: 'image' }],
    gen_slot: { node_id: 'p1', index: 0 },
  },
};

function seed(): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    nodes: [PROMPT, SLOT],
    connections: [],
    selection: [],
  });
}

function nodeById(id: string): Record<string, unknown> | undefined {
  return useCanvasCoreStore.getState().nodes.find(
    (n) => (n as CanvasNode).id === id,
  ) as Record<string, unknown> | undefined;
}

afterEach(() => {
  useCanvasCoreStore.getState().reset();
  useRegenStore.getState().reset();
});

describe('promptIdForOutput', () => {
  it('resolves the source prompt from the gen_slot tag', () => {
    seed();
    expect(promptIdForOutput('out1')).toBe('p1');
  });

  it('returns null for non-slot outputs', () => {
    seed();
    expect(promptIdForOutput('p1')).toBeNull();
  });
});

describe('regenerateForOutput', () => {
  it('re-runs the prompt and archives the old image into history', async () => {
    seed();
    const calls: string[] = [];
    useRegenStore.setState({
      runnerOverride: async (ctx): Promise<RunnerResult> => {
        calls.push(ctx.body);
        return { ok: true, text: '', error: null, urls: ['/gm/2/cover'], media_kind: 'image' };
      },
    });

    const ok = await regenerateForOutput('out1');

    expect(ok).toBe(true);
    expect(calls).toEqual(['a cat']);
    const slot = nodeById('out1');
    const data = slot?.data as { images?: Array<{ url: string }> };
    expect(data.images?.map((i) => i.url)).toEqual(['/gm/2/cover']);
    const history = useCanvasCoreStore.getState().nodes.find(
      (n) => ((n as CanvasNode).data as { history_for?: string })?.history_for === 'out1',
    );
    expect(history).toBeTruthy();
    const promptData = nodeById('p1')?.data as { run_status?: string };
    expect(promptData.run_status).toBe('succeeded');
  });

  it('does not write the slot when the canvas switched mid-run', async () => {
    seed();
    useRegenStore.setState({
      runnerOverride: async (): Promise<RunnerResult> => {
        // Simulate the user opening ANOTHER canvas while the run is in flight.
        useCanvasCoreStore.setState({ canvasId: 'other' });
        return { ok: true, text: '', error: null, urls: ['/gm/2/cover'], media_kind: 'image' };
      },
    });

    const ok = await regenerateForOutput('out1');

    expect(ok).toBe(false);
    const slot = nodeById('out1');
    const data = slot?.data as { images?: Array<{ url: string }> };
    expect(data.images?.map((i) => i.url)).toEqual(['/gm/1/cover']);
  });

  it('is re-entrancy-safe per output node', async () => {
    seed();
    let resolveRun: (() => void) | null = null;
    let runs = 0;
    useRegenStore.setState({
      runnerOverride: async (): Promise<RunnerResult> => {
        runs += 1;
        await new Promise<void>((r) => {
          resolveRun = r;
        });
        return { ok: true, text: '', error: null, urls: ['/gm/2/cover'], media_kind: 'image' };
      },
    });

    const first = regenerateForOutput('out1');
    const second = await regenerateForOutput('out1'); // rejected while in flight
    expect(second).toBe(false);
    resolveRun?.();
    await first;
    expect(runs).toBe(1);
    // Keyed per canvas ('9' seeded) so same-id nodes on other canvases stay usable.
    expect(useRegenStore.getState().running['9:out1']).toBeFalsy();
  });

  it('marks the prompt failed (and returns false) on a failed run', async () => {
    seed();
    useRegenStore.setState({
      runnerOverride: async (): Promise<RunnerResult> => ({
        ok: false,
        text: '',
        error: 'quota exceeded',
      }),
    });

    const ok = await regenerateForOutput('out1');

    expect(ok).toBe(false);
    const promptData = nodeById('p1')?.data as { run_status?: string; run_error?: string };
    expect(promptData.run_status).toBe('failed');
    expect(promptData.run_error).toBe('quota exceeded');
  });
});
