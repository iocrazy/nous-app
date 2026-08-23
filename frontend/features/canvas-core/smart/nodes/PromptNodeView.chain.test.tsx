// IC 一键运行: the chain-tail prompt shows a Chain button; non-tails don't.
import { fireEvent, render, screen } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { PromptNodeView } from './PromptNodeView';

vi.mock('./useGenerationModels', () => ({ useGenerationModels: () => [] }));
vi.mock('./useTextModels', () => ({ useTextModels: () => [] }));
vi.mock('./useAgents', () => ({ useAgents: () => [] }));

const P = (id: string) => ({
  id, type: 'prompt', position: { x: 0, y: 0 },
  data: { body: 'x', run_status: 'idle', provider_slug: '', agent_id: null, resource_refs: [] },
});
const O = (id: string) => ({ id, type: 'output', position: { x: 0, y: 0 }, data: {} });

function seed(withChain: boolean) {
  useCanvasCoreStore.setState({
    canvasId: 'c1',
    nodes: (withChain ? [P('p1'), O('o1'), P('p2')] : [P('p2')]) as never,
    connections: (withChain
      ? [{ id: 'e1', source: 'p1', target: 'o1' }, { id: 'e2', source: 'o1', target: 'p2' }]
      : []) as never,
  });
}

const props = (id: string) => {
  const node = useCanvasCoreStore.getState().nodes.find(
    (n) => (n as { id: string }).id === id,
  ) as { data: unknown };
  return { id, data: node.data, selected: false } as unknown as Parameters<
    typeof PromptNodeView
  >[0];
};

describe('PromptNodeView chain button', () => {
  beforeEach(() => seed(true));

  it('shows Chain on the cascade tail', () => {
    render(<ReactFlowProvider><PromptNodeView {...props('p2')} /></ReactFlowProvider>);
    expect(screen.getByTestId('prompt-node-run-chain')).toBeInTheDocument();
  });

  it('hides Chain on a lone prompt', () => {
    seed(false);
    render(<ReactFlowProvider><PromptNodeView {...props('p2')} /></ReactFlowProvider>);
    expect(screen.queryByTestId('prompt-node-run-chain')).toBeNull();
  });
});

it('split-enabled prompt shows the separator row with a live item count', () => {
  seed(false);
  const p = props('p2');
  (p.data as Record<string, unknown>).split_enabled = true;
  (p.data as Record<string, unknown>).body = 'a cat; a dog; a bird';
  render(<ReactFlowProvider><PromptNodeView {...p} /></ReactFlowProvider>);
  expect(screen.getByTestId('prompt-split-row')).toBeInTheDocument();
  expect(screen.getByTestId('prompt-split-count').textContent).toContain('3');
  // the toggle pill is present for flipping it off
  expect(screen.getByTestId('prompt-split-toggle')).toBeInTheDocument();
});

it('persists a dragged textarea height (IC promptH)', () => {
  seed(false);
  const p = props('p2');
  render(<ReactFlowProvider><PromptNodeView {...p} /></ReactFlowProvider>);
  const ta = screen.getByRole('textbox', { name: /prompt/i });
  ta.getBoundingClientRect = () =>
    ({ height: 180, width: 300, top: 0, left: 0, right: 300, bottom: 180, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
  fireEvent.mouseUp(ta);
  const node = useCanvasCoreStore.getState().nodes.find(
    (n) => (n as { id: string }).id === 'p2',
  ) as { data: { body_h?: number } };
  expect(node.data.body_h).toBe(180);
});
