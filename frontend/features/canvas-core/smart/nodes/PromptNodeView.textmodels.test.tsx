// features/canvas-core/smart/nodes/PromptNodeView.textmodels.test.tsx
// P0-1: the text prompt's model dropdown is sourced from the platform DB
// catalog (useTextModels), NOT a hardcoded PROVIDER_OPTIONS list. This guards
// the 2026-07-12 regression where the default named `qwen-plus` and a
// fabricated `nous/storyboard` slug the platform doesn't carry.

import { ReactFlowProvider } from '@xyflow/react';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { changeUiSelect, uiSelectMirror } from '../../../../tests/uiSelect';

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
vi.mock('./useAgents', () => ({ useAgents: () => [] }));
vi.mock('./useTextModels', () => ({
  useTextModels: () => [
    { name: 'mediahub-doubao-llm', display_name: 'Doubao LLM', type: 'llm', actual_provider: 'doubao' },
    { name: 'mediahub-deepseek', display_name: 'DeepSeek', type: 'llm', actual_provider: 'deepseek' },
  ],
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

describe('PromptNodeView text-model dropdown (P0-1)', () => {
  it('renders the catalog default plus DB llm rows — no hardcoded slugs', () => {
    seedAndRender(TEXT_DATA);
    const select = uiSelectMirror('Prompt provider');
    const options = Array.from(select.options).map((o) => ({ value: o.value, label: o.textContent }));
    expect(options).toEqual([
      { value: '', label: 'Catalog default' },
      { value: 'mediahub-doubao-llm', label: 'Doubao LLM' },
      { value: 'mediahub-deepseek', label: 'DeepSeek' },
    ]);
    // The retired hardcoded values must be gone.
    const values = options.map((o) => o.value);
    expect(values).not.toContain('qwen/qwen-plus');
    expect(values).not.toContain('nous/storyboard');
  });

  it('defaults to the empty catalog-default value', () => {
    seedAndRender(TEXT_DATA);
    expect(uiSelectMirror('Prompt provider').value).toBe('');
  });

  it('selecting a model persists its bare catalog name into provider_slug', () => {
    seedAndRender(TEXT_DATA);
    changeUiSelect('Prompt provider', 'mediahub-deepseek');
    expect(nodeData().provider_slug).toBe('mediahub-deepseek');
  });
});
