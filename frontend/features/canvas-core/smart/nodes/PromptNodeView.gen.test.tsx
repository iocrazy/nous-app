// features/canvas-core/smart/nodes/PromptNodeView.gen.test.tsx
// Prompt generation settings UI (G4-F1): a kind selector (Text/Image/Video)
// swaps the provider row for the generation model + params controls, all
// persisted into data.gen (absent = legacy text prompt).

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
vi.mock('./useGenerationModels', () => ({
  useGenerationModels: (kind?: string) =>
    [
      { name: 'jimeng-cli-image', display_name: 'Jimeng Image', type: 'image', actual_provider: 'jimeng-cli' },
      { name: 'jimeng-cli-seedance', display_name: 'Seedance', type: 'video', actual_provider: 'jimeng-cli' },
    ].filter((m) => !kind || m.type === kind),
}));
vi.mock('./useAgents', () => ({ useAgents: () => [] }));
vi.mock('./useTextModels', () => ({ useTextModels: () => [] }));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { PromptNodeView } from './PromptNodeView';

const baseProps = {
  selected: false,
  dragging: false,
  zIndex: 0,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  deletable: true,
  draggable: true,
  selectable: true,
} as const;

const TEXT_DATA = {
  body: 'x',
  provider_slug: '',
  agent_id: null,
  run_status: 'idle',
  resource_refs: [],
};

function seedAndRender(data: Record<string, unknown>) {
  useCanvasCoreStore.setState({
    kind: 'smart',
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
  const node = useCanvasCoreStore.getState().nodes[0] as Record<string, Record<string, unknown>>;
  return node.data;
}

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
});
afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

describe('PromptNodeView generation settings', () => {
  it('defaults to Text and keeps the provider select', () => {
    seedAndRender(TEXT_DATA);
    expect((screen.getByLabelText('Prompt kind') as HTMLSelectElement).value).toBe('text');
    expect(screen.getByLabelText('Prompt provider')).toBeTruthy();
  });

  it('switching to Image writes data.gen and shows model + ratio + count', () => {
    seedAndRender(TEXT_DATA);
    fireEvent.change(screen.getByLabelText('Prompt kind'), { target: { value: 'image' } });
    expect((nodeData().gen as { kind: string }).kind).toBe('image');
  });

  it('renders image controls and patches model/ratio/count', () => {
    const data = { ...TEXT_DATA, gen: { kind: 'image', model: '', ratio: '16:9', count: 1 } };
    seedAndRender(data);

    const model = screen.getByLabelText('Generation model') as HTMLSelectElement;
    expect(model.options.length).toBeGreaterThan(0);
    fireEvent.change(model, { target: { value: 'jimeng-cli-image' } });
    expect((nodeData().gen as { model: string }).model).toBe('jimeng-cli-image');

    fireEvent.change(screen.getByLabelText('Aspect ratio'), { target: { value: '9:16' } });
    expect((nodeData().gen as { ratio: string }).ratio).toBe('9:16');

    fireEvent.change(screen.getByLabelText('Image count'), { target: { value: '4' } });
    expect((nodeData().gen as { count: number }).count).toBe(4);
  });

  it('video kind shows aspect control (no count)', () => {
    const data = { ...TEXT_DATA, gen: { kind: 'video', model: '', aspect: '16:9' } };
    seedAndRender(data);
    expect(screen.getByLabelText('Video aspect')).toBeTruthy();
    expect(screen.queryByLabelText('Image count')).toBeNull();
  });

  it('switching back to Text clears gen', () => {
    const data = { ...TEXT_DATA, gen: { kind: 'image', model: '', count: 1 } };
    seedAndRender(data);
    fireEvent.change(screen.getByLabelText('Prompt kind'), { target: { value: 'text' } });
    expect(nodeData().gen).toBeNull();
  });
});
