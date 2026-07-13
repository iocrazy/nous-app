/**
 * LlmNodeView — the standalone LLM card (dual-canvas Phase 2.1). Pins:
 * Run assembles upstream prompt/llm text + own input and lands the
 * backend text into data.output_text; failures land in run_error.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { LlmNodeView, upstreamTextFor } from './LlmNodeView';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';

vi.mock('@xyflow/react', () => ({
  Handle: () => null,
  Position: { Left: 'left', Right: 'right' },
}));
vi.mock('./useTextModels', () => ({ useTextModels: () => [] }));
vi.mock('./useAgents', () => ({ useAgents: () => [] }));

const mockCaller = vi.hoisted(() => vi.fn());
vi.mock('../runner.backend', () => ({
  createBackendRunner: () => mockCaller,
}));

function seed(data: Record<string, unknown>, extraNodes: unknown[] = [], connections: unknown[] = []) {
  useCanvasCoreStore.setState({
    canvasId: 'c1',
    nodes: [
      { id: 'l1', type: 'llm', position: { x: 0, y: 0 }, data },
      ...extraNodes,
    ],
    connections,
  } as never);
}

function nodeData(): Record<string, unknown> {
  return (useCanvasCoreStore.getState().nodes[0] as { data: Record<string, unknown> }).data;
}

function renderView() {
  const props = {
    id: 'l1',
    data: nodeData(),
    selected: false,
  } as unknown as Parameters<typeof LlmNodeView>[0];
  return render(<LlmNodeView {...props} />);
}

const BASE = {
  provider_slug: '',
  agent_id: null,
  input_text: '',
  output_text: '',
  run_status: 'idle',
  run_error: null,
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useCanvasCoreStore.getState().reset();
});

describe('upstreamTextFor', () => {
  it('collects prompt bodies and llm outputs wired into the node, skipping empties', () => {
    const nodes = [
      { id: 'p1', type: 'prompt', data: { body: 'scene text' } },
      { id: 'l0', type: 'llm', data: { output_text: 'prior answer' } },
      { id: 'm1', type: 'media', data: { items: [] } },
      { id: 'p2', type: 'prompt', data: { body: '   ' } },
    ];
    const conns = [
      { source: 'p1', target: 'l1' },
      { source: 'l0', target: 'l1' },
      { source: 'm1', target: 'l1' },
      { source: 'p2', target: 'l1' },
    ];
    expect(upstreamTextFor('l1', nodes, conns as never)).toEqual([
      'scene text',
      'prior answer',
    ]);
  });
});

describe('LlmNodeView', () => {
  it('Run sends own input to the backend and lands the output text', async () => {
    mockCaller.mockResolvedValue({ ok: true, text: 'the answer', error: null });
    seed({ ...BASE, input_text: 'what day is it?' });
    renderView();

    fireEvent.click(screen.getByRole('button', { name: /Run/ }));
    await waitFor(() => {
      expect(mockCaller).toHaveBeenCalledWith(
        expect.objectContaining({ promptId: 'l1', body: 'what day is it?' }),
      );
      expect(nodeData().output_text).toBe('the answer');
      expect(nodeData().run_status).toBe('succeeded');
    });
  });

  it('Run prepends wired upstream prompt text', async () => {
    mockCaller.mockResolvedValue({ ok: true, text: 'ok', error: null });
    seed(
      { ...BASE, input_text: 'continue' },
      [{ id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: { body: 'upstream' } }],
      [{ id: 'e1', source: 'p1', target: 'l1' }],
    );
    renderView();

    fireEvent.click(screen.getByRole('button', { name: /Run/ }));
    await waitFor(() => {
      expect(mockCaller).toHaveBeenCalledWith(
        expect.objectContaining({ body: 'upstream\n\ncontinue' }),
      );
    });
  });

  it('a failed run lands run_error and does not clobber output', async () => {
    mockCaller.mockResolvedValue({ ok: false, text: '', error: 'model down' });
    seed({ ...BASE, input_text: 'x', output_text: 'old' });
    renderView();

    fireEvent.click(screen.getByRole('button', { name: /Run/ }));
    await waitFor(() => {
      expect(nodeData().run_status).toBe('failed');
      expect(nodeData().run_error).toBe('model down');
      expect(nodeData().output_text).toBe('old');
    });
  });

  it('empty input with no upstream is a no-op', () => {
    seed({ ...BASE });
    renderView();
    fireEvent.click(screen.getByRole('button', { name: /Run/ }));
    expect(mockCaller).not.toHaveBeenCalled();
  });
});
