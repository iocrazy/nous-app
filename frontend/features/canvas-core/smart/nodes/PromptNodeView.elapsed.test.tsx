// features/canvas-core/smart/nodes/PromptNodeView.elapsed.test.tsx
// Run-time pill (P1-2, Infinite's .run-time-pill): live seconds while the
// prompt runs, final duration pinned in green once it succeeds. The data
// stamps (run_started_at / run_finished_at) have existed since Phase 2 —
// the smart node just never showed them.

import { ReactFlowProvider } from '@xyflow/react';
import { render, screen, cleanup } from '@testing-library/react';
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

function renderWith(data: Record<string, unknown>) {
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

const BASE = {
  body: 'x',
  provider_slug: '',
  agent_id: null,
  resource_refs: [],
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-07-11T10:01:05Z'));
  useCanvasCoreStore.getState().reset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  useCanvasCoreStore.getState().reset();
});

describe('PromptNodeView run-time pill (P1-2)', () => {
  it('shows live elapsed seconds while running', () => {
    renderWith({
      ...BASE,
      run_status: 'running',
      run_started_at: '2026-07-11T10:00:00Z',
      run_finished_at: null,
    });
    expect(screen.getByTestId('prompt-elapsed').textContent).toBe('1m 05s');
  });

  it('pins the final duration once succeeded', () => {
    renderWith({
      ...BASE,
      run_status: 'succeeded',
      run_started_at: '2026-07-11T10:00:00Z',
      run_finished_at: '2026-07-11T10:00:42Z',
    });
    const pill = screen.getByTestId('prompt-elapsed');
    expect(pill.textContent).toBe('42s');
    expect(pill.className).toContain('emerald');
  });

  it('renders no pill when idle', () => {
    renderWith({ ...BASE, run_status: 'idle', run_started_at: null, run_finished_at: null });
    expect(screen.queryByTestId('prompt-elapsed')).toBeNull();
  });
});
