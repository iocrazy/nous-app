// features/canvas-core/smart/nodes/PromptNodeView.negative.test.tsx
// Phase 2 asset prompt management: PromptNode gains an optional
// `negative_body` field. The node view shows a small negative-prompt
// textarea below the main body ONLY when negative_body is non-empty
// (absent/legacy nodes render nothing extra), and edits patch through
// the existing useNodeDataPatch → store.patchNode path.

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

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
  useGenerationModels: () => [],
}));

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

const BASE_DATA = {
  body: 'pos',
  provider_slug: '',
  agent_id: null,
  run_status: 'idle',
  resource_refs: [],
};

function setNode(data: Record<string, unknown>) {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    loadStatus: 'ready',
    nodes: [{ id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data }],
    connections: [],
    selection: [],
  });
}

afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

describe('PromptNodeView negative_body', () => {
  it('renders negative textarea only when negative_body is non-empty', () => {
    const withNegative = { ...BASE_DATA, negative_body: 'lowres' };
    setNode(withNegative);
    const { container, unmount } = render(
      <ReactFlowProvider>
        <PromptNodeView {...baseProps} id="p1" type="prompt" data={withNegative} />
      </ReactFlowProvider>,
    );
    const negTextarea = container.querySelector(
      'textarea[placeholder="Negative prompt"]',
    ) as HTMLTextAreaElement | null;
    expect(negTextarea).toBeTruthy();
    expect(negTextarea!.value).toBe('lowres');
    unmount();

    setNode(BASE_DATA);
    const { container: container2 } = render(
      <ReactFlowProvider>
        <PromptNodeView {...baseProps} id="p1" type="prompt" data={BASE_DATA} />
      </ReactFlowProvider>,
    );
    expect(container2.querySelector('textarea[placeholder="Negative prompt"]')).toBeNull();
  });

  it('keeps the negative textarea visible after it is cleared to an empty string (I3)', () => {
    // A negative_body key that is explicitly '' (e.g. the user cleared the
    // box) must still render the textarea — only an ABSENT key means "no
    // negative prompt was ever set". Losing the box on clear would be a
    // dead end with no way to type a negative prompt back in.
    const clearedNegative = { ...BASE_DATA, negative_body: '' };
    setNode(clearedNegative);
    const { container } = render(
      <ReactFlowProvider>
        <PromptNodeView {...baseProps} id="p1" type="prompt" data={clearedNegative} />
      </ReactFlowProvider>,
    );
    const negTextarea = container.querySelector(
      'textarea[placeholder="Negative prompt"]',
    ) as HTMLTextAreaElement | null;
    expect(negTextarea).toBeTruthy();
    expect(negTextarea!.value).toBe('');
  });

  it('editing negative textarea patches negative_body', () => {
    const withNegative = { ...BASE_DATA, negative_body: 'lowres' };
    setNode(withNegative);
    const { container } = render(
      <ReactFlowProvider>
        <PromptNodeView {...baseProps} id="p1" type="prompt" data={withNegative} />
      </ReactFlowProvider>,
    );
    const negTextarea = container.querySelector(
      'textarea[placeholder="Negative prompt"]',
    ) as HTMLTextAreaElement;
    fireEvent.change(negTextarea, { target: { value: 'blurry, watermark' } });

    const node = useCanvasCoreStore.getState().nodes.find((n) => (n as { id: string }).id === 'p1');
    expect((node?.data as { negative_body?: string }).negative_body).toBe('blurry, watermark');
  });
});
