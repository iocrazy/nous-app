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
import { LoopNodeView } from './LoopNodeView';
import { PromptNodeView } from './PromptNodeView';
import { ShotNodeView } from './ShotNodeView';

// PromptNodeView now imports useResourceSearch for the @-mention picker.
// Return empty data so the debounced fetch never fires and existing tests
// are unaffected.
vi.mock('../../../../hooks/useResourceSearch', () => ({
  useResourceSearch: vi.fn().mockReturnValue({
    data: {
      results: [],
      counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
      next_cursor: null,
    },
    loading: false,
    error: null,
  }),
}));

// ResourcePickerSuggestion (rendered by CanvasMentionPicker) uses useTranslation.
// Passthrough so existing tests that don't open the picker are unaffected.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '4242',
    kind: 'smart',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-10T12:00:00+00:00',
  });
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  useCanvasCoreStore.getState().reset();
});

function Wrap({ children }: { children: ReactNode }) {
  // React Flow components rely on its context (ReactFlowProvider) and
  // its CSS; rendering Handle outside of it warns. Wrap in the
  // provider so the test stays clean.
  return <ReactFlowProvider>{children}</ReactFlowProvider>;
}

function seedNode(id: string, type: string, data: Record<string, unknown>) {
  useCanvasCoreStore.setState({
    nodes: [{ id, type, data, position: { x: 0, y: 0 } }],
  });
}

const baseProps = {
  type: 'placeholder',
  dragHandle: undefined,
  draggable: true,
  selectable: true,
  deletable: true,
  selected: false,
  dragging: false,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  width: 240,
  height: 100,
  zIndex: 0,
} as const;

// ============================================================
// Shot node edit
// ============================================================

describe('ShotNodeView — edit affordances', () => {
  it('typing into title patches the node data via the store', () => {
    seedNode('shot1', 'shot', {
      title: 'Old',
      reference_resource_ids: [],
      notes: '',
    });
    render(
      <Wrap>
        <ShotNodeView
          {...baseProps}
          id="shot1"
          type="shot"
          data={{ title: 'Old', reference_resource_ids: [], notes: '' }}
        />
      </Wrap>,
    );
    fireEvent.change(screen.getByLabelText('Shot title'), {
      target: { value: 'New Wide Shot' },
    });
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    expect(node.data.title).toBe('New Wide Shot');
  });

  it('typing into notes patches', () => {
    seedNode('shot1', 'shot', {
      title: 't',
      reference_resource_ids: [],
      notes: 'a',
    });
    render(
      <Wrap>
        <ShotNodeView
          {...baseProps}
          id="shot1"
          type="shot"
          data={{ title: 't', reference_resource_ids: [], notes: 'a' }}
        />
      </Wrap>,
    );
    fireEvent.change(screen.getByLabelText('Shot notes'), {
      target: { value: 'a robot in a foggy alley' },
    });
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    expect(node.data.notes).toBe('a robot in a foggy alley');
  });
});

// ============================================================
// Prompt node edit
// ============================================================

describe('PromptNodeView — edit affordances', () => {
  const defaultData = {
    body: '',
    provider_slug: '',
    agent_id: null,
    run_status: 'idle',
    run_started_at: null,
    run_finished_at: null,
    run_error: null,
  };

  it('typing into the body patches', () => {
    seedNode('p1', 'prompt', { ...defaultData });
    render(
      <Wrap>
        <PromptNodeView
          {...baseProps}
          id="p1"
          type="prompt"
          data={{ ...defaultData }}
        />
      </Wrap>,
    );
    fireEvent.change(screen.getByLabelText('Prompt body'), {
      target: { value: 'describe a robot' },
    });
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    expect(node.data.body).toBe('describe a robot');
  });

  it('changing provider via dropdown patches provider_slug', () => {
    seedNode('p1', 'prompt', { ...defaultData });
    render(
      <Wrap>
        <PromptNodeView
          {...baseProps}
          id="p1"
          type="prompt"
          data={{ ...defaultData }}
        />
      </Wrap>,
    );
    fireEvent.change(screen.getByLabelText('Prompt provider'), {
      target: { value: 'nous/storyboard' },
    });
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    expect(node.data.provider_slug).toBe('nous/storyboard');
  });

  it('renders run_error when present', () => {
    seedNode('p1', 'prompt', {
      ...defaultData,
      run_status: 'failed',
      run_error: 'rate limited',
    });
    render(
      <Wrap>
        <PromptNodeView
          {...baseProps}
          id="p1"
          type="prompt"
          data={{
            ...defaultData,
            run_status: 'failed',
            run_error: 'rate limited',
          }}
        />
      </Wrap>,
    );
    expect(screen.getByText('rate limited')).toBeInTheDocument();
  });
});

// ============================================================
// Loop node edit
// ============================================================

describe('LoopNodeView — edit affordances', () => {
  it('changing mode dropdown patches mode', () => {
    seedNode('l1', 'loop', { mode: 'serial', label: '' });
    render(
      <Wrap>
        <LoopNodeView
          {...baseProps}
          id="l1"
          type="loop"
          data={{ mode: 'serial', label: '' }}
        />
      </Wrap>,
    );
    fireEvent.change(screen.getByLabelText('Loop mode'), {
      target: { value: 'parallel' },
    });
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    expect(node.data.mode).toBe('parallel');
  });

  it('typing into label patches', () => {
    seedNode('l1', 'loop', { mode: 'serial', label: '' });
    render(
      <Wrap>
        <LoopNodeView
          {...baseProps}
          id="l1"
          type="loop"
          data={{ mode: 'serial', label: '' }}
        />
      </Wrap>,
    );
    fireEvent.change(screen.getByLabelText('Loop label'), {
      target: { value: 'each shot' },
    });
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    expect(node.data.label).toBe('each shot');
  });
});

// ============================================================
// History invariant
// ============================================================

describe('inline edits do NOT push undo history', () => {
  it('typing into prompt body does not bump canUndo', () => {
    seedNode('p1', 'prompt', {
      body: '',
      provider_slug: '',
      agent_id: null,
      run_status: 'idle',
      run_started_at: null,
      run_finished_at: null,
      run_error: null,
    });
    expect(useCanvasCoreStore.getState().canUndo()).toBe(false);
    render(
      <Wrap>
        <PromptNodeView
          {...baseProps}
          id="p1"
          type="prompt"
          data={{
            body: '',
            provider_slug: '',
            agent_id: null,
            run_status: 'idle',
            run_started_at: null,
            run_finished_at: null,
            run_error: null,
          }}
        />
      </Wrap>,
    );
    fireEvent.change(screen.getByLabelText('Prompt body'), {
      target: { value: 'x' },
    });
    // patchNode is the inline-edit path; the store contract is that
    // patchNode never pushes onto history (runtime/inline state is
    // not user-undoable).
    expect(useCanvasCoreStore.getState().canUndo()).toBe(false);
  });
});
