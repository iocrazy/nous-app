// features/canvas-core/smart/nodes/PromptNodeView.theme.test.tsx
// Light-theme contrast regression: the `ink` ladder self-flips under
// [data-theme="light"] (index.css remaps --ink-800 → #f0f0f2), so the
// zinc-era idiom `text-ink-800 dark:text-ink-200` renders the prompt body
// near-white on a white card in light mode. On a flipped ladder the class
// must be a single value (text-ink-200) with no dark: override.

import { ReactFlowProvider } from '@xyflow/react';
import { render } from '@testing-library/react';
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

const DATA = {
  body: 'contrast probe',
  provider_slug: '',
  agent_id: null,
  run_status: 'idle',
  resource_refs: [],
};

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    loadStatus: 'ready',
    nodes: [{ id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: DATA }],
    connections: [],
    selection: [],
  });
});
afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

describe('PromptNodeView theme-safe text classes', () => {
  it('body editor uses a single flipped-ladder ink value (no dark: override)', () => {
    const { container } = render(
      <ReactFlowProvider>
        <PromptNodeView {...baseProps} id="p1" type="prompt" data={DATA} />
      </ReactFlowProvider>,
    );
    // The body is a tiptap contenteditable now; the colour requirement is
    // unchanged and still lives on the editable element.
    const body = container.querySelector('[data-testid="prompt-body-editor"]');
    expect(body).toBeTruthy();
    const cls = body!.className;
    // ink-800 flips to #f0f0f2 in light mode — invisible body text.
    expect(cls).not.toMatch(/text-ink-[6789]\d\d/);
    expect(cls).not.toContain('dark:text-ink');
    expect(cls).toContain('text-ink-200');
  });
});
