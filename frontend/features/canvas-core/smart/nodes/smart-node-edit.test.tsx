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

// The text prompt's provider dropdown is now sourced from the platform DB
// catalog (P0-1). Stub the hook with fixed llm rows so the dropdown has
// selectable options without a network fetch.
vi.mock('./useAgents', () => ({ useAgents: () => [] }));
vi.mock('./useTextModels', () => ({
  useTextModels: () => [
    { name: 'mediahub-doubao-llm', display_name: 'Doubao LLM', type: 'llm', actual_provider: 'doubao' },
  ],
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
    // UiSelect: open the trigger, then pick the option (its onChange fires from
    // the portal option click, not a native <select> change event).
    fireEvent.click(screen.getByLabelText('Prompt provider'));
    fireEvent.click(screen.getByRole('option', { name: 'Doubao LLM' }));
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    expect(node.data.provider_slug).toBe('mediahub-doubao-llm');
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
  it('clicking the segmented control patches mode', () => {
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
    // The IC card replaced the mode dropdown with a segmented control —
    // "Serial"/"Parallel" are stable (untranslated) aria-labels.
    fireEvent.click(screen.getByLabelText('Parallel'));
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
// Loop batch fields (Infinite parity Phase 1 G3a)
// ============================================================

describe('LoopNodeView — batch fields', () => {
  const LEGACY = { mode: 'serial', label: '' };
  const FULL = { mode: 'serial', label: '', rounds: 3, round_start: 1, prompts: ['first'] };

  function nodeData(): Record<string, unknown> {
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    return node.data;
  }

  it('renders legacy data (mode+label only) with safe defaults', () => {
    seedNode('l1', 'loop', LEGACY);
    render(
      <Wrap>
        <LoopNodeView {...baseProps} id="l1" type="loop" data={LEGACY} />
      </Wrap>,
    );
    // The i18n mock returns the key itself, so the (now-translated)
    // Rounds/Start aria-labels render as their i18n keys.
    expect((screen.getByLabelText('canvas.loopRounds') as HTMLInputElement).value).toBe('1');
    expect((screen.getByLabelText('canvas.loopStart') as HTMLInputElement).value).toBe('1');
  });

  it('editing rounds patches a clamped value', () => {
    seedNode('l1', 'loop', FULL);
    render(
      <Wrap>
        <LoopNodeView {...baseProps} id="l1" type="loop" data={FULL} />
      </Wrap>,
    );
    fireEvent.change(screen.getByLabelText('canvas.loopRounds'), { target: { value: '500' } });
    expect(nodeData().rounds).toBe(100);
  });

  it('editing start index patches a clamped value', () => {
    seedNode('l1', 'loop', FULL);
    render(
      <Wrap>
        <LoopNodeView {...baseProps} id="l1" type="loop" data={FULL} />
      </Wrap>,
    );
    fireEvent.change(screen.getByLabelText('canvas.loopStart'), { target: { value: '0' } });
    expect(nodeData().round_start).toBe(1);
  });

  it('adds and edits a rotating prompt', () => {
    seedNode('l1', 'loop', FULL);
    render(
      <Wrap>
        <LoopNodeView {...baseProps} id="l1" type="loop" data={FULL} />
      </Wrap>,
    );
    fireEvent.click(screen.getByLabelText('Add prompt'));
    expect(nodeData().prompts).toEqual(['first', '']);

    fireEvent.change(screen.getByLabelText('Loop prompt 1'), {
      target: { value: '生成第《计数》张' },
    });
    expect((nodeData().prompts as string[])[0]).toBe('生成第《计数》张');
  });

  it('removes a prompt but keeps at least one entry deletable-free', () => {
    seedNode('l1', 'loop', { ...FULL, prompts: ['a', 'b'] });
    render(
      <Wrap>
        <LoopNodeView
          {...baseProps}
          id="l1"
          type="loop"
          data={{ ...FULL, prompts: ['a', 'b'] }}
        />
      </Wrap>,
    );
    fireEvent.click(screen.getByLabelText('Remove prompt 2'));
    expect(nodeData().prompts).toEqual(['a']);
  });

  it('disables removal of the last remaining prompt (matches Infinite)', () => {
    seedNode('l1', 'loop', { ...FULL, prompts: ['only'] });
    render(
      <Wrap>
        <LoopNodeView
          {...baseProps}
          id="l1"
          type="loop"
          data={{ ...FULL, prompts: ['only'] }}
        />
      </Wrap>,
    );
    expect(screen.getByLabelText('Remove prompt 1')).toBeDisabled();
  });
});

// ============================================================
// Loop node IC card (segmented mode, image/prompt toggles)
// ============================================================

describe('LoopNodeView IC card', () => {
  const FULL = {
    mode: 'serial',
    label: '',
    show_prompt: true,
    image_input: false,
    image_batch_size: 1,
    rounds: 1,
    round_start: 1,
    prompts: [''],
  };

  function nodeData(): Record<string, unknown> {
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    return node.data;
  }

  it('toggles image_input and shows the image panel note', () => {
    seedNode('l1', 'loop', FULL);
    render(
      <Wrap>
        <LoopNodeView {...baseProps} id="l1" type="loop" data={FULL} />
      </Wrap>,
    );
    // image panel hidden until toggled (image_input: false)
    expect(screen.queryByText('canvas.loopImageEmpty')).toBeNull();
    fireEvent.click(screen.getByLabelText('Toggle image input'));
    expect(nodeData().image_input).toBe(true);
  });

  it('switches run mode via the segmented control', () => {
    seedNode('l1', 'loop', FULL);
    render(
      <Wrap>
        <LoopNodeView {...baseProps} id="l1" type="loop" data={FULL} />
      </Wrap>,
    );
    fireEvent.click(screen.getByLabelText('Parallel'));
    expect(nodeData().mode).toBe('parallel');
  });

  it('toggles show_prompt off via the prompt toggle pill', () => {
    seedNode('l1', 'loop', FULL);
    render(
      <Wrap>
        <LoopNodeView {...baseProps} id="l1" type="loop" data={FULL} />
      </Wrap>,
    );
    expect(screen.getByLabelText('Loop prompt 1')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Toggle prompt input'));
    expect(nodeData().show_prompt).toBe(false);
  });

  it('shows the "will output N" note once an upstream media node feeds the loop with image_input on', () => {
    // Only /api/v1/generated-media/ URLs qualify as durable i2i sources
    // (resolveSourceUrls / durableImagesOf) — see promptInputs.ts.
    useCanvasCoreStore.setState({
      nodes: [
        {
          id: 'm1',
          type: 'media',
          data: {
            title: 'Media',
            items: [{ url: '/api/v1/generated-media/1.png', kind: 'image' }],
          },
          position: { x: 0, y: 0 },
        },
        { id: 'l1', type: 'loop', data: { ...FULL, image_input: true }, position: { x: 0, y: 0 } },
      ],
      connections: [{ id: 'c1', source: 'm1', target: 'l1' }],
    });
    render(
      <Wrap>
        <LoopNodeView {...baseProps} id="l1" type="loop" data={{ ...FULL, image_input: true }} />
      </Wrap>,
    );
    // The i18n mock ignores interpolation args, so it renders the raw key —
    // this still proves the "has upstream images" branch (not the empty-state
    // "loopImageEmpty" copy) was chosen, which is what resolveSourceUrls wiring
    // is being tested for here.
    expect(screen.getByText('canvas.loopImageWillOutput')).toBeInTheDocument();
    expect(screen.queryByText('canvas.loopImageEmpty')).toBeNull();
  });

  it('inserts the literal 《计数》 counter token into the last prompt', () => {
    seedNode('l1', 'loop', FULL);
    render(
      <Wrap>
        <LoopNodeView {...baseProps} id="l1" type="loop" data={FULL} />
      </Wrap>,
    );
    fireEvent.click(screen.getByLabelText('Insert count token'));
    expect((nodeData().prompts as string[])[0]).toBe('《计数》');
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
