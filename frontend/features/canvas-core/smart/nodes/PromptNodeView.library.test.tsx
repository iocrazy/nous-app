// features/canvas-core/smart/nodes/PromptNodeView.library.test.tsx
// Phase 2 Task 3: PromptNodeView gets a Library pill button (lucide Library
// icon, same CANVAS_PILL_TRIGGER styling as the other toolbar pills) that
// opens AssetPromptPicker. This only covers the open-on-click wiring — the
// pick→apply path is covered by loadPromptAsset.test.ts (pure function) and
// AssetPromptPicker.test.tsx (picker UI).

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../../hooks/useResourceSearch', () => ({
  useResourceSearch: vi.fn().mockReturnValue({
    data: { results: [], counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 }, next_cursor: null },
    loading: false,
    error: null,
  }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));
vi.mock('./useGenerationModels', () => ({
  useGenerationModels: () => [],
}));
vi.mock('../../../../services/resourceService', () => ({
  fetchPromptAssets: vi.fn().mockResolvedValue([]),
  getResourceCoverUrl: (id: string) => `https://api.test/cover/${id}`,
}));
vi.mock('../../../../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([]),
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

function setNode() {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    loadStatus: 'ready',
    nodes: [{ id: 'p1', type: 'prompt', position: { x: 400, y: 200 }, data: BASE_DATA }],
    connections: [],
    selection: [],
  });
}

afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

describe('PromptNodeView Library button', () => {
  it('opens AssetPromptPicker on click', () => {
    setNode();
    render(
      <ReactFlowProvider>
        <PromptNodeView {...baseProps} id="p1" type="prompt" data={BASE_DATA} />
      </ReactFlowProvider>,
    );

    expect(screen.queryByTestId('asset-prompt-picker')).toBeNull();
    fireEvent.click(screen.getByTestId('prompt-library-button'));
    expect(screen.getByTestId('asset-prompt-picker')).toBeTruthy();
  });
});
