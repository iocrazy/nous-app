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
    // Real text-models row shape (2026-09-24): actual_model + last_test_status
    // ride along; actual_provider never does (leak tripwire).
    { name: 'mediahub-doubao-llm', display_name: 'Doubao LLM', actual_model: 'doubao-seed-1-6-250615', type: 'llm', last_test_status: 'ok' },
    { name: 'mediahub-deepseek', display_name: 'DeepSeek', actual_model: 'deepseek-v4-pro', type: 'llm', last_test_status: 'not_probed' },
    // Local nous-engine row, authorized but not loaded (mig 503).
    { name: 'nous-qwen3-8-27b', display_name: 'Qwen3 27B', actual_model: 'qwen3-8-27b', type: 'llm', last_test_status: 'idle' },
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
      // Labelled exactly like the admin AI Models card (actual_model, no
      // display_name); the value stays the row name.
      { value: 'mediahub-doubao-llm', label: 'doubao-seed-1-6-250615' },
      { value: 'mediahub-deepseek', label: 'deepseek-v4-pro' },
      { value: 'nous-qwen3-8-27b', label: 'qwen3-8-27b' },
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

describe('PromptNodeView text-model dropdown — idle rows', () => {
  it('shows an idle row disabled with the not-loaded reason', () => {
    seedAndRender(TEXT_DATA);
    const options = Array.from(uiSelectMirror('Prompt provider').options);
    const idle = options.find((o) => o.value === 'nous-qwen3-8-27b')!;
    expect(idle.disabled).toBe(true);
    // react-i18next is mocked to echo keys here.
    expect(idle.dataset.description).toBe('platformModel.notLoaded');
    expect(options.find((o) => o.value === 'mediahub-deepseek')!.disabled).toBe(false);
  });

  it('keeps a saved idle model selected and titles the trigger', () => {
    seedAndRender({ ...TEXT_DATA, provider_slug: 'nous-qwen3-8-27b' });
    expect(uiSelectMirror('Prompt provider').value).toBe('nous-qwen3-8-27b');
    expect(screen.getByLabelText('Prompt provider').getAttribute('title')).toBe(
      'platformModel.notLoaded',
    );
  });
});
