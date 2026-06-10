import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { CanvasComposer } from './CanvasComposer';
import { _resetIdCounter } from './factories';
import type { PromptCaller } from './runner';

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

describe('CanvasComposer — add nodes', () => {
  it('renders four Add buttons + Run + Cascade Run', () => {
    render(<CanvasComposer />);
    expect(screen.getByRole('button', { name: /\+ shot/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /\+ prompt/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /\+ output/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /\+ loop/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^run$/i })).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /cascade run/i }),
    ).toBeInTheDocument();
  });

  it('clicking "+ Shot" appends a shot node + selects it', () => {
    render(<CanvasComposer />);
    fireEvent.click(screen.getByRole('button', { name: /\+ shot/i }));
    const state = useCanvasCoreStore.getState();
    expect(state.nodes).toHaveLength(1);
    const node = state.nodes[0] as Record<string, unknown>;
    expect(node.type).toBe('shot');
    expect(state.selection).toEqual([node.id]);
  });

  it('+ Loop appends a loop node defaulting to serial mode', () => {
    render(<CanvasComposer />);
    fireEvent.click(screen.getByRole('button', { name: /\+ loop/i }));
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    expect(node.type).toBe('loop');
    expect((node.data as Record<string, unknown>).mode).toBe('serial');
  });

  it('toolbar is role="toolbar" with an accessible name', () => {
    render(<CanvasComposer />);
    expect(
      screen.getByRole('toolbar', { name: /smart canvas composer/i }),
    ).toBeInTheDocument();
  });
});

describe('CanvasComposer — Run', () => {
  it('Run is disabled when nothing is selected', () => {
    render(<CanvasComposer />);
    const btn = screen.getByRole('button', { name: /^run$/i });
    expect(btn).toBeDisabled();
  });

  it('Run only fires for selected prompt ids; non-prompt selection is ignored', async () => {
    const caller: PromptCaller = vi.fn(async () => ({
      ok: true,
      text: '',
      error: null,
    }));
    useCanvasCoreStore.setState({
      nodes: [
        { id: 'p1', type: 'prompt', data: { body: '', run_status: 'idle' } },
        { id: 'p2', type: 'prompt', data: { body: '', run_status: 'idle' } },
        { id: 's1', type: 'shot', data: { title: 'x' } },
      ],
      selection: ['p1', 's1'], // shot must be ignored
    });
    render(<CanvasComposer runner={caller} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
    });
    await waitFor(() => expect(caller).toHaveBeenCalledTimes(1));
    const ctx = (caller as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(ctx.promptId).toBe('p1');
  });

  it('Cascade Run sorts prompts in topo order and runs all of them', async () => {
    const calls: string[] = [];
    const caller: PromptCaller = vi.fn(async (ctx) => {
      calls.push(ctx.promptId);
      return { ok: true, text: '', error: null };
    });
    useCanvasCoreStore.setState({
      nodes: [
        { id: 'pZ', type: 'prompt', data: { body: '', run_status: 'idle' } },
        { id: 'pA', type: 'prompt', data: { body: '', run_status: 'idle' } },
      ],
      connections: [
        { id: 'eA-Z', source: 'pA', target: 'pZ' },
      ],
    });
    render(<CanvasComposer runner={caller} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /cascade run/i }));
    });
    await waitFor(() => expect(caller).toHaveBeenCalledTimes(2));
    expect(calls).toEqual(['pA', 'pZ']);
  });

  it('the prompt run_status moves to succeeded on success', async () => {
    const caller: PromptCaller = vi.fn(async () => ({
      ok: true,
      text: '',
      error: null,
    }));
    useCanvasCoreStore.setState({
      nodes: [
        { id: 'p1', type: 'prompt', data: { body: '', run_status: 'idle' } },
      ],
      selection: ['p1'],
    });
    render(<CanvasComposer runner={caller} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
    });
    await waitFor(() => {
      const p1 = useCanvasCoreStore.getState().nodes[0] as Record<
        string,
        Record<string, unknown>
      >;
      expect(p1.data.run_status).toBe('succeeded');
    });
  });
});
