// features/canvas-core/smart/nodes/PromptNodeView.dropped.test.tsx
//
// What the backend dropped, the user sees.
//
// P2 writes `dropped_knobs` into the generation task's metadata; this repo has
// shipped the "backend returns it, frontend never reads it" failure three times
// before (attachment_failures, session freshness, the deleted endpoint). The
// badge next to the run status is the consumer that closes the loop: dispatch
// is where the dropping happened, so the run badge is where the answer lives.
//
// The negative-prompt cases below are the OTHER half of the same honesty rule
// and they are deliberately invariant pins, not new behaviour — see the
// comment on that describe block.

import { ReactFlowProvider } from '@xyflow/react';
import { render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import en from '../../../../public/locales/en.json';

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
// Resolve against the REAL shipped English copy rather than a hand-written
// table, so a missing/renamed key surfaces here as a failing assertion instead
// of a raw `canvas.ignoredKnobs` reaching users (the NousCenterVerifyPanel
// pattern). `t` is created once so it stays referentially stable across
// renders, exactly like the real hook.
vi.mock('react-i18next', () => {
  const t = (
    key: string,
    varsOrDefault?: Record<string, unknown> | string,
  ): string => {
    // i18next's second argument is EITHER interpolation vars or a string
    // default. The production code uses both forms, so a mock that only
    // understands one would make an unknown drop-reason look like a bug it
    // is not (or hide one that is).
    const fallback = typeof varsOrDefault === 'string' ? varsOrDefault : key;
    const vars = typeof varsOrDefault === 'string' ? undefined : varsOrDefault;
    const template = key
      .split('.')
      .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en);
    if (typeof template !== 'string') return fallback;
    return template.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(vars?.[name] ?? ''));
  };
  return { useTranslation: () => ({ t }) };
});
vi.mock('./useGenerationModels', () => ({
  useGenerationModels: () => [],
}));

// Real dispatch/poll are the only things stubbed for the seam test at the
// bottom — everything between the poll response and the rendered badge is the
// production path.
const dispatchGenerations = vi.fn();
const pollGeneration = vi.fn();
vi.mock('../../services/canvasGenerationService', async () => {
  const actual = await vi.importActual<
    typeof import('../../services/canvasGenerationService')
  >('../../services/canvasGenerationService');
  return {
    ...actual,
    dispatchGenerations: (...a: unknown[]) => dispatchGenerations(...a),
    pollGeneration: (...a: unknown[]) => pollGeneration(...a),
  };
});

// A model that honours nothing optional — the negative box cases below need a
// caps answer of `negative: false` to mean anything. Shape copied from the
// endpoint's real projection (ModelCapabilities), not a convenient subset.
type Caps = import('../../services/canvasGenerationService').ModelCapabilities;
const caps = vi.fn<() => Caps | null>(() => ({
  ratios: ['1:1'],
  quality: false,
  resolution: false,
  max_refs: 9,
  negative: false,
  video_modes: [],
}));
vi.mock('./useModelCapabilities', () => ({
  useModelCapabilities: () => caps(),
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { markDroppedKnobs } from '../droppedKnobs';
import { withGenerationRunner } from '../generationRunner';
import { resumePendingGenerations } from '../genResume';
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
  // Not 'idle': RunStatusBadge renders nothing for an idle node, and the
  // dropped badge's whole point is that it sits beside that badge.
  run_status: 'succeeded',
  resource_refs: [],
  gen: { kind: 'image', model: 'codex-local-image', ratio: '1:1', count: 1 },
};

function mount(data: Record<string, unknown>) {
  useCanvasCoreStore.getState().reset();
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

afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

describe('PromptNodeView dropped-knob badge', () => {
  it('names the knobs the backend ignored, beside the run status badge', () => {
    const { getByTestId } = mount({ ...BASE_DATA, last_dropped: ['quality'] });

    const badge = getByTestId('dropped-knobs-badge');
    expect(badge.textContent).toBe('Ignored: quality');
    // Tooltip goes through i18n too — a zh user must not get an English
    // sentence hanging off a translated badge.
    expect(badge.getAttribute('title')).toBe('Not supported by this model');
    // Beside the run badge, not somewhere else on the card: the run is what
    // the answer is about.
    expect(
      badge.parentElement,
      'the dropped badge is not in the same container as the run status badge',
    ).toBe(getByTestId('run-status-badge').parentElement);
  });

  it('lists every dropped knob', () => {
    const { getByTestId } = mount({
      ...BASE_DATA,
      last_dropped: ['ratio', 'quality', 'refs'],
    });
    expect(getByTestId('dropped-knobs-badge').textContent).toBe(
      'Ignored: ratio, quality, refs',
    );
  });

  it('says nothing when the run dropped nothing', () => {
    const { queryByTestId, unmount } = mount({ ...BASE_DATA, last_dropped: [] });
    expect(queryByTestId('dropped-knobs-badge')).toBeNull();
    unmount();

    // ...and for a node that predates the field entirely.
    const { queryByTestId: q2 } = mount(BASE_DATA);
    expect(q2('dropped-knobs-badge')).toBeNull();
  });
});

// References the run could not USE (asset-library P4 Task 3).
//
// The canvas can now be fed `/api/v1/resources/{id}/cover` references from
// asset nodes, and those have failure modes a generated-media url does not:
// out of scope, no image bytes behind the row, storage unreadable. The
// backend reports each as `{url, reason}` in `dropped_refs`; without a
// consumer here the picture would simply come back missing a reference and
// nothing would say so — the "选了也生成了但图里没有" failure, again.
describe('PromptNodeView dropped-reference badge', () => {
  it('names how many references were dropped and why', () => {
    const { getByTestId } = mount({
      ...BASE_DATA,
      last_dropped_refs: [
        { url: '/api/v1/resources/91/cover', reason: 'not_in_scope' },
      ],
    });

    const badge = getByTestId('dropped-knobs-badge');
    expect(badge.textContent).toBe('Ignored: 1 reference(s) (out of scope)');
    // The urls themselves are in the tooltip: the badge is a summary, but the
    // user still has to be able to find out WHICH picture went missing.
    expect(badge.getAttribute('title')).toBe(
      '/api/v1/resources/91/cover — not_in_scope',
    );
  });

  it('groups several drops by reason', () => {
    const { getByTestId } = mount({
      ...BASE_DATA,
      last_dropped_refs: [
        { url: '/api/v1/resources/91/cover', reason: 'not_in_scope' },
        { url: '/api/v1/resources/92/cover', reason: 'not_in_scope' },
        { url: '/api/v1/resources/93/cover', reason: 'no_image_file' },
      ],
    });
    expect(getByTestId('dropped-knobs-badge').textContent).toBe(
      'Ignored: 2 reference(s) (out of scope), 1 reference(s) (no image file)',
    );
  });

  it('shows knobs and references together — the two are orthogonal', () => {
    const { getByTestId } = mount({
      ...BASE_DATA,
      last_dropped: ['quality'],
      last_dropped_refs: [
        { url: '/api/v1/resources/91/cover', reason: 'materialize_failed' },
      ],
    });
    expect(getByTestId('dropped-knobs-badge').textContent).toBe(
      'Ignored: quality, 1 reference(s) (unreadable)',
    );
  });

  it('renders an unlabelled reason as its raw code rather than omitting it', () => {
    // A backend that adds a reason before the locale does must still produce a
    // visible badge. Swallowing the entry would be the silent drop this whole
    // field exists to prevent.
    const { getByTestId } = mount({
      ...BASE_DATA,
      last_dropped_refs: [{ url: '/api/v1/resources/91/cover', reason: 'brand_new' }],
    });
    expect(getByTestId('dropped-knobs-badge').textContent).toBe(
      'Ignored: 1 reference(s) (brand_new)',
    );
  });

  it('every reason the backend can emit has copy in BOTH locales', async () => {
    // The backend's vocabulary is fixed (canvas_generation.py's header block).
    // A code with no label is not a crash, it just leaks an identifier at the
    // user — so the parity check is here, where the list is read.
    const zh = (await import('../../../../public/locales/zh.json')).default;
    for (const reason of [
      'unknown_shape',
      'not_in_scope',
      'no_image_file',
      'materialize_failed',
      'scope_unresolved',
      'unresolved',
    ]) {
      expect(
        (en as Record<string, never>).canvas['refDropReason'][reason],
        `en is missing canvas.refDropReason.${reason}`,
      ).toBeTruthy();
      expect(
        (zh as Record<string, never>).canvas['refDropReason'][reason],
        `zh is missing canvas.refDropReason.${reason}`,
      ).toBeTruthy();
    }
  });

  it('says nothing when every reference was used', () => {
    const { queryByTestId } = mount({ ...BASE_DATA, last_dropped_refs: [] });
    expect(queryByTestId('dropped-knobs-badge')).toBeNull();
  });
});

// The negative prompt box under a model that cannot honour it.
//
// ⚠️ These are INVARIANT PINS, not coverage of new code. The P4 plan assumed
// the node had an "add a negative box" affordance to hide when the model says
// `negative: false`; it does not — `negative_body` only ever becomes defined
// from OUTSIDE this component (a library asset carrying gen_prompt_negative
// via handlePickAsset, or createPromptNode from Send-to-Canvas). So there is
// nothing to gate, and the rule that matters is the other one: a box holding
// the user's words must never disappear because a capability lookup said no.
// If someone later adds that affordance, the second case here is what tells
// them it has to be capability-gated.
const negTextarea = (c: HTMLElement) =>
  c.querySelector('textarea[placeholder="Negative prompt"]');

describe('PromptNodeView negative box vs capabilities', () => {
  it('keeps an existing negative prompt visible even when the model cannot honour it', () => {
    const { container } = mount({ ...BASE_DATA, negative_body: 'lowres, watermark' });
    const box = negTextarea(container) as HTMLTextAreaElement | null;
    expect(box, 'the negative box vanished under negative:false — that swallows the user’s text').toBeTruthy();
    expect(box!.value).toBe('lowres, watermark');
  });

  it('shows no negative box at all for a node that never had one', () => {
    // Deliberately NOT asserting the absence of some named add-affordance:
    // no such testid exists, so that assertion could never fail — including
    // in the future it would be written for, where the affordance arrives
    // under a different name. The pin here is the visible state; the note to
    // whoever adds an entry point is the comment on this describe block.
    const { container } = mount(BASE_DATA);
    expect(negTextarea(container)).toBeNull();
  });

  it('renders exactly as it does today when capabilities are unknown', () => {
    caps.mockReturnValueOnce(null);
    const { container, unmount } = mount({ ...BASE_DATA, negative_body: 'lowres' });
    expect((negTextarea(container) as HTMLTextAreaElement).value).toBe('lowres');
    unmount();

    caps.mockReturnValueOnce(null);
    const { container: c2 } = mount(BASE_DATA);
    expect(negTextarea(c2)).toBeNull();
  });
});

describe('a real run puts its dropped knobs on the node (seam)', () => {
  // The two halves above are each correct in isolation and still add up to
  // nothing if they disagree about the field name — which is precisely how
  // "the backend returns it, the frontend never reads it" survives a green
  // suite. This is the one case that fails if either end is unwired.
  it('shows what the backend ignored after the run that produced it', async () => {
    useCanvasCoreStore.getState().reset();
    useCanvasCoreStore.setState({
      kind: 'smart',
      canvasId: '9',
      loadStatus: 'ready',
      nodes: [{ id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: { ...BASE_DATA } }],
      connections: [],
      selection: [],
    });
    dispatchGenerations.mockResolvedValue(['t1']);
    pollGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: { result_url: '/gm/1/cover', dropped_knobs: ['quality'] },
    });

    const runner = withGenerationRunner(
      async () => ({ ok: true, text: '', error: null }),
      { canvasId: '9', onDropped: markDroppedKnobs },
    );
    await runner({
      promptId: 'p1',
      body: 'a cat',
      provider_slug: '',
      agent_id: null,
      gen: { kind: 'image', model: 'ark', count: 1 },
    });

    const node = useCanvasCoreStore
      .getState()
      .nodes.find((n) => (n as unknown as { id: string }).id === 'p1');
    const data = (node as unknown as { data: Record<string, unknown> }).data;
    const { getByTestId } = render(
      <ReactFlowProvider>
        <PromptNodeView {...baseProps} id="p1" type="prompt" data={data} />
      </ReactFlowProvider>,
    );
    expect(getByTestId('dropped-knobs-badge').textContent).toBe('Ignored: quality');
  });

  it('shows a dropped REFERENCE after the run that produced it', async () => {
    // Same seam, other ledger. The two halves (backend field name, node data
    // field name, badge copy) have to agree end to end or this is green in
    // isolation and blank in the app.
    useCanvasCoreStore.getState().reset();
    useCanvasCoreStore.setState({
      kind: 'smart',
      canvasId: '9',
      loadStatus: 'ready',
      nodes: [{ id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: { ...BASE_DATA } }],
      connections: [],
      selection: [],
    });
    dispatchGenerations.mockResolvedValue(['t1']);
    pollGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: {
        result_url: '/gm/1/cover',
        dropped_knobs: [],
        dropped_refs: [
          { url: '/api/v1/resources/91/cover', reason: 'not_in_scope' },
        ],
      },
    });

    const runner = withGenerationRunner(
      async () => ({ ok: true, text: '', error: null }),
      { canvasId: '9', onDropped: markDroppedKnobs },
    );
    await runner({
      promptId: 'p1',
      body: 'a cat',
      provider_slug: '',
      agent_id: null,
      gen: { kind: 'image', model: 'ark', count: 1 },
    });

    const node = useCanvasCoreStore
      .getState()
      .nodes.find((n) => (n as unknown as { id: string }).id === 'p1');
    const data = (node as unknown as { data: Record<string, unknown> }).data;
    expect(data.last_dropped_refs).toEqual([
      { url: '/api/v1/resources/91/cover', reason: 'not_in_scope' },
    ]);
    const { getByTestId } = render(
      <ReactFlowProvider>
        <PromptNodeView {...baseProps} id="p1" type="prompt" data={data} />
      </ReactFlowProvider>,
    );
    expect(getByTestId('dropped-knobs-badge').textContent).toBe(
      'Ignored: 1 reference(s) (out of scope)',
    );
  });
});

describe('a resumed run puts its dropped knobs on the node (seam)', () => {
  // A reload is the OTHER way a generation reaches a terminal phase, and it
  // does not go through the runner at all — genResume re-attaches polling to
  // the persisted task ids. `last_dropped: []` (written by the dispatch-time
  // clear) is persisted with the node, so a resume that ignored
  // `dropped_knobs` would bring the page back showing a CLEAN badge for a run
  // that dropped knobs: a wrong answer, not a missing one.
  const seedResumable = (data: Record<string, unknown>) => {
    useCanvasCoreStore.getState().reset();
    useCanvasCoreStore.setState({
      kind: 'smart',
      canvasId: '9',
      loadStatus: 'ready',
      nodes: [
        {
          id: 'p1',
          type: 'prompt',
          position: { x: 0, y: 0 },
          data: {
            ...BASE_DATA,
            run_status: 'running',
            gen_tasks: [{ task_id: 't1', kind: 'image' }],
            ...data,
          },
        },
      ],
      connections: [],
      selection: [],
    });
  };
  const renderFromStore = () => {
    const node = useCanvasCoreStore
      .getState()
      .nodes.find((n) => (n as unknown as { id: string }).id === 'p1');
    const data = (node as unknown as { data: Record<string, unknown> }).data;
    return render(
      <ReactFlowProvider>
        <PromptNodeView {...baseProps} id="p1" type="prompt" data={data} />
      </ReactFlowProvider>,
    );
  };

  it('shows what the backend ignored for a run that finished after a reload', async () => {
    seedResumable({ last_dropped: [] });
    pollGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: { result_url: '/gm/1/cover', dropped_knobs: ['quality'] },
    });

    await resumePendingGenerations();

    expect(renderFromStore().getByTestId('dropped-knobs-badge').textContent).toBe(
      'Ignored: quality',
    );
  });

  it('leaves the badge alone when every resumed poll broke', async () => {
    // Seeded non-empty so "untouched" is observable at all; the realistic
    // post-reload value is []. Either way the rule is the same as the
    // runner's: nobody reported, so nothing gets written down.
    seedResumable({ last_dropped: ['quality'] });
    pollGeneration.mockRejectedValue(new Error('poll broke'));

    await resumePendingGenerations();

    expect(renderFromStore().getByTestId('dropped-knobs-badge').textContent).toBe(
      'Ignored: quality',
    );
  });
});
