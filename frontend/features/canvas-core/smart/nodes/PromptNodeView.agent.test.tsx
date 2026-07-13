// features/canvas-core/smart/nodes/PromptNodeView.agent.test.tsx
// CC3: text-mode agent picker — sourced from the AI Library, writes the
// pre-plumbed agent_id channel (server injects the agent's IDENTITY/SOUL).

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../../hooks/useResourceSearch', () => ({
  useResourceSearch: vi.fn().mockReturnValue({
    data: { results: [], counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 }, next_cursor: null },
    loading: false,
    error: null,
  }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));
vi.mock('./useGenerationModels', () => ({ useGenerationModels: () => [] }));
vi.mock('./useTextModels', () => ({ useTextModels: () => [] }));
vi.mock('./useAgents', () => ({
  useAgents: () => [
    { id: 'a1', slug: 'character-persona', name: 'Character Persona' },
    { id: 'a2', slug: 'character-portrait', name: 'Character Portrait' },
  ],
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { PromptNodeView } from './PromptNodeView';

const baseProps = {
  selected: false, dragging: false, zIndex: 0, isConnectable: true,
  positionAbsoluteX: 0, positionAbsoluteY: 0, deletable: true,
  draggable: true, selectable: true,
} as const;

const TEXT_DATA = {
  body: 'x',
  provider_slug: '',
  agent_id: null,
  run_status: 'idle',
  resource_refs: [],
};

function seedAndRender(data: Record<string, unknown> = TEXT_DATA) {
  useCanvasCoreStore.setState({
    kind: 'character',
    canvasId: '9',
    loadStatus: 'ready',
    nodes: [{ id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data }],
    connections: [],
    selection: [],
  });
  return render(
    <ReactFlowProvider>
      <PromptNodeView {...baseProps} id="p1" type="prompt" data={data} />
    </ReactFlowProvider>,
  );
}

function nodeData(): Record<string, unknown> {
  return (useCanvasCoreStore.getState().nodes[0] as Record<string, any>).data;
}

beforeEach(() => useCanvasCoreStore.getState().reset());
afterEach(() => useCanvasCoreStore.getState().reset());

describe('PromptNodeView agent picker (CC3)', () => {
  it('lists No agent + the AI Library agents', () => {
    seedAndRender();
    const select = screen.getByLabelText('Prompt agent') as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      'No agent',
      'Character Persona',
      'Character Portrait',
    ]);
    expect(select.value).toBe('');
  });

  it('selecting an agent persists its id; clearing writes null', () => {
    seedAndRender();
    fireEvent.change(screen.getByLabelText('Prompt agent'), { target: { value: 'a1' } });
    expect(nodeData().agent_id).toBe('a1');
    fireEvent.change(screen.getByLabelText('Prompt agent'), { target: { value: '' } });
    expect(nodeData().agent_id).toBeNull();
  });

  it('image mode hides the agent picker (generation has no persona hook)', () => {
    seedAndRender({ ...TEXT_DATA, gen: { kind: 'image', model: '', ratio: '1:1', count: 1 } });
    expect(screen.queryByLabelText('Prompt agent')).toBeNull();
  });
});
