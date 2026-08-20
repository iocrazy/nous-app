// IC 一键运行: the chain-tail prompt shows a Chain button; non-tails don't.
import { render, screen } from '@testing-library/react';
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

const props = (id: string) =>
  ({ id, data: useCanvasCoreStore.getState().nodes.find((n) => (n as { id: string }).id === id)!
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      .data, selected: false } as any);

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
