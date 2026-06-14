/**
 * PromptNodeView — @-mention picker integration tests.
 *
 * Covers the full flow:
 *   type @ → picker shown → pick resource → ref persisted in node data
 *
 * Uses a synchronous mock for useResourceSearch so tests are deterministic
 * without needing to advance fake timers through the 150ms debounce.
 */

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { PromptNodeView } from './PromptNodeView';
import type { ResourceSearchResponse, ResourceSearchResult } from '../../../../types';

// ── Module mocks ──────────────────────────────────────────────────────────────

// i18n: passthrough — ResourcePickerSuggestion uses t() for tab labels
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// Synchronous stub for useResourceSearch.
// Default: no results. Tests override via mockReturnValue before render.
vi.mock('../../../../hooks/useResourceSearch', () => ({
  useResourceSearch: vi.fn(),
}));

// Import AFTER vi.mock so we get the mocked version
import { useResourceSearch } from '../../../../hooks/useResourceSearch';

// ── Helpers ───────────────────────────────────────────────────────────────────

const EMPTY_SEARCH: ResourceSearchResponse = {
  results: [],
  counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
  next_cursor: null,
};

const FAKE_RESULT: ResourceSearchResult = {
  id: 'r1',
  name: 'robot-concept.jpg',
  kind: 'image',
  mime: 'image/jpeg',
  size: 2048,
  scope: { type: 'personal', id: 'u1' },
  updated_at: new Date().toISOString(),
  thumbnail_url: null,
};

const FAKE_RESULT_2: ResourceSearchResult = {
  id: 'r2',
  name: 'canyon-sunset.mp4',
  kind: 'video',
  mime: 'video/mp4',
  size: 10240,
  scope: { type: 'personal', id: 'u1' },
  updated_at: new Date().toISOString(),
  thumbnail_url: null,
};

function makeSearchData(results: ResourceSearchResult[]): {
  data: ResourceSearchResponse;
  loading: boolean;
  error: null;
} {
  return {
    data: {
      results,
      counts: {
        all: results.length,
        video: results.filter((r) => r.kind === 'video').length,
        image: results.filter((r) => r.kind === 'image').length,
        doc: 0,
        audio: 0,
        pdf: 0,
      },
      next_cursor: null,
    },
    loading: false,
    error: null,
  };
}

function Wrap({ children }: { children: ReactNode }) {
  return <ReactFlowProvider>{children}</ReactFlowProvider>;
}

function seedPromptNode(id: string, overrides: Record<string, unknown> = {}) {
  const data = {
    body: '',
    provider_slug: '',
    agent_id: null,
    run_status: 'idle',
    run_started_at: null,
    run_finished_at: null,
    run_error: null,
    resource_refs: [],
    ...overrides,
  };
  useCanvasCoreStore.setState({
    nodes: [{ id, type: 'prompt', data, position: { x: 0, y: 0 } }],
  });
  return data;
}

const BASE_PROPS = {
  type: 'prompt',
  dragHandle: undefined,
  draggable: true,
  selectable: true,
  deletable: true,
  selected: false,
  dragging: false,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  width: 280,
  height: 160,
  zIndex: 0,
} as const;

function renderPromptNode(id: string, data: Record<string, unknown>) {
  return render(
    <Wrap>
      <PromptNodeView {...BASE_PROPS} id={id} type="prompt" data={data} />
    </Wrap>,
  );
}

// ── Setup / Teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: 'test-canvas',
    kind: 'smart',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-14T00:00:00+00:00',
  });
  // Default: no results
  vi.mocked(useResourceSearch).mockReturnValue(makeSearchData([]));
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  useCanvasCoreStore.getState().reset();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('PromptNodeView — @-mention picker', () => {
  it('picker is hidden on initial render', () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);
    expect(screen.queryByTestId('canvas-mention-picker')).toBeNull();
  });

  it('typing @ opens the picker', () => {
    vi.mocked(useResourceSearch).mockReturnValue(makeSearchData([FAKE_RESULT]));
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    fireEvent.change(screen.getByLabelText('Prompt body'), {
      target: { value: '@' },
    });

    expect(screen.getByTestId('canvas-mention-picker')).toBeInTheDocument();
    expect(screen.getByTestId('resource-picker')).toBeInTheDocument();
  });

  it('picker shows search results returned by useResourceSearch', () => {
    vi.mocked(useResourceSearch).mockReturnValue(makeSearchData([FAKE_RESULT, FAKE_RESULT_2]));
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    fireEvent.change(screen.getByLabelText('Prompt body'), {
      target: { value: '@' },
    });

    // Two result rows rendered
    const rows = screen.getAllByTestId('resource-picker-row');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('robot-concept.jpg');
    expect(rows[1]).toHaveTextContent('canyon-sunset.mp4');
  });

  it('picking a resource replaces the @-token in the body', () => {
    vi.mocked(useResourceSearch).mockReturnValue(makeSearchData([FAKE_RESULT]));
    const data = seedPromptNode('p1', { body: '' });
    renderPromptNode('p1', data);

    const textarea = screen.getByLabelText('Prompt body');
    fireEvent.change(textarea, { target: { value: '@robot' } });

    const row = screen.getByTestId('resource-picker-row');
    fireEvent.click(row);

    // textarea value should now contain "@robot-concept.jpg " (replaced)
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    expect((node.data.body as string)).toBe('@robot-concept.jpg ');
  });

  it('picking a resource persists a PromptResourceRef in node data', () => {
    vi.mocked(useResourceSearch).mockReturnValue(makeSearchData([FAKE_RESULT]));
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    fireEvent.change(screen.getByLabelText('Prompt body'), {
      target: { value: '@' },
    });
    fireEvent.click(screen.getByTestId('resource-picker-row'));

    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    const refs = node.data.resource_refs as unknown[];
    expect(refs).toHaveLength(1);
    expect(refs[0]).toMatchObject({
      resource_id: 'r1',
      name: 'robot-concept.jpg',
      kind: 'image',
      mime: 'image/jpeg',
      scope: { type: 'personal', id: 'u1' },
    });
  });

  it('picker closes after picking a resource', () => {
    vi.mocked(useResourceSearch).mockReturnValue(makeSearchData([FAKE_RESULT]));
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    fireEvent.change(screen.getByLabelText('Prompt body'), {
      target: { value: '@' },
    });
    expect(screen.getByTestId('canvas-mention-picker')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('resource-picker-row'));
    expect(screen.queryByTestId('canvas-mention-picker')).toBeNull();
  });

  it('Escape key closes the picker without selecting', () => {
    vi.mocked(useResourceSearch).mockReturnValue(makeSearchData([FAKE_RESULT]));
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    const textarea = screen.getByLabelText('Prompt body');
    fireEvent.change(textarea, { target: { value: '@' } });
    expect(screen.getByTestId('canvas-mention-picker')).toBeInTheDocument();

    fireEvent.keyDown(textarea, { key: 'Escape' });
    expect(screen.queryByTestId('canvas-mention-picker')).toBeNull();

    // No refs added
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    expect(node.data.resource_refs).toEqual([]);
  });

  it('typing text without @ keeps picker hidden', () => {
    vi.mocked(useResourceSearch).mockReturnValue(makeSearchData([FAKE_RESULT]));
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    fireEvent.change(screen.getByLabelText('Prompt body'), {
      target: { value: 'hello world' },
    });
    expect(screen.queryByTestId('canvas-mention-picker')).toBeNull();
  });

  it('removing @ from the text closes the picker', () => {
    vi.mocked(useResourceSearch).mockReturnValue(makeSearchData([FAKE_RESULT]));
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    const textarea = screen.getByLabelText('Prompt body');
    fireEvent.change(textarea, { target: { value: '@foo' } });
    expect(screen.getByTestId('canvas-mention-picker')).toBeInTheDocument();

    // Remove the @ — plain text again
    fireEvent.change(textarea, { target: { value: 'foo' } });
    expect(screen.queryByTestId('canvas-mention-picker')).toBeNull();
  });

  it('duplicate resource is not added twice', () => {
    vi.mocked(useResourceSearch).mockReturnValue(makeSearchData([FAKE_RESULT]));
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    const textarea = screen.getByLabelText('Prompt body');

    // First pick
    fireEvent.change(textarea, { target: { value: '@' } });
    fireEvent.click(screen.getByTestId('resource-picker-row'));

    // Second pick of the same resource
    fireEvent.change(textarea, { target: { value: '@' } });
    fireEvent.click(screen.getByTestId('resource-picker-row'));

    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    // Still only one ref
    expect((node.data.resource_refs as unknown[]).length).toBe(1);
  });

  it('ref chip renders for each resource_refs entry', () => {
    const data = seedPromptNode('p1', {
      resource_refs: [
        {
          resource_id: 'r1',
          name: 'robot-concept.jpg',
          kind: 'image',
          mime: 'image/jpeg',
          scope: { type: 'personal', id: 'u1' },
        },
      ],
    });
    renderPromptNode('p1', data);

    expect(screen.getByTestId('prompt-ref-chips')).toBeInTheDocument();
    expect(screen.getByTestId('prompt-ref-chip')).toHaveTextContent(
      '@robot-concept.jpg',
    );
  });

  it('clicking × on a ref chip removes the ref from node data', () => {
    const data = seedPromptNode('p1', {
      resource_refs: [
        {
          resource_id: 'r1',
          name: 'robot-concept.jpg',
          kind: 'image',
          mime: 'image/jpeg',
          scope: { type: 'personal', id: 'u1' },
        },
      ],
    });
    renderPromptNode('p1', data);

    fireEvent.mouseDown(
      screen.getByLabelText('Remove reference to robot-concept.jpg'),
    );

    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    expect((node.data.resource_refs as unknown[])).toHaveLength(0);
  });

  it('existing tests still pass — patchNode does not push undo history', () => {
    // Regression guard: the mention wiring must not break the existing invariant
    // that inline edits do not push onto the undo stack.
    const data = seedPromptNode('p1');
    expect(useCanvasCoreStore.getState().canUndo()).toBe(false);
    renderPromptNode('p1', data);

    fireEvent.change(screen.getByLabelText('Prompt body'), {
      target: { value: 'some text' },
    });
    expect(useCanvasCoreStore.getState().canUndo()).toBe(false);
  });
});
