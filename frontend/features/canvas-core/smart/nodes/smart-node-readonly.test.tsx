/**
 * Read-only sessions must not leave dead buttons inside smart nodes.
 *
 * #1828 withdrew the canvas-LEVEL gestures (drag / connect / delete /
 * paste) and the chrome that creates nodes, and disabled ShotNodeView's
 * Generate + Promote. Everything else INSIDE the node renderers stayed
 * live: a viewer could type into a prompt body, pick a model, hit Run,
 * Crop, Add, Retry — and nothing happened. The store's `markDirty` bails
 * on `readOnly`, so the edit never became a revision and no PUT ever left;
 * the click was swallowed in silence. That is the exact failure mode
 * CLAUDE.md bans ("用户动作 → 触发的每条路径必须返回类型化结果,
 * silent no-op 不可接受").
 *
 * This is the fence around the fix, table-driven over EVERY type in
 * `SMART_NODE_TYPES` (the registry is the source of truth for "which
 * renderers exist" — an inventory typed out by hand is how a new node type
 * quietly escapes the rule). For each write affordance it asserts:
 *
 *   readOnly:false → present AND enabled   (the fix didn't break editing)
 *   readOnly:true  → disabled, or gone     (no lit button that does nothing)
 *
 * View-only affordances get the opposite treatment: they must survive a
 * read-only session, because withholding them would take away *reading*,
 * which the viewer is entitled to.
 */

import { ReactFlowProvider } from '@xyflow/react';
import { render, screen } from '@testing-library/react';
import type { ComponentType, ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { SMART_NODE_TYPES } from './registry';

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

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, fallback?: string) => fallback ?? k }),
}));

vi.mock('./useAgents', () => ({ useAgents: () => [] }));
vi.mock('./useTextModels', () => ({ useTextModels: () => [] }));
vi.mock('./useGenerationModels', () => ({ useGenerationModels: () => [] }));

// OutputNodeView hides Rerun unless the output traces back to a prompt.
// Stub the lookup so the Rerun affordance actually renders here.
vi.mock('../regenerate', () => ({
  promptIdForOutput: () => 'p1',
  regenerateForOutput: vi.fn(),
  rerunPrompt: vi.fn(),
}));

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '4242',
    kind: 'smart',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-08-13T12:00:00+00:00',
  });
});

afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

function Wrap({ children }: { children: ReactNode }) {
  return <ReactFlowProvider>{children}</ReactFlowProvider>;
}

const baseProps = {
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

/** How to find one affordance. `label` = aria-label, `testId` = data-testid. */
type Locator = { label: string } | { testId: string };

interface NodeCase {
  /** Row name — also the `type` prop React Flow would pass. */
  name: string;
  /** Key into SMART_NODE_TYPES; several rows may share one renderer. */
  type: keyof typeof SMART_NODE_TYPES;
  data: Record<string, unknown>;
  /** Anything that writes the document or dispatches a run. */
  writes: Array<{ what: string } & Locator>;
  /** Pure viewing — must stay usable for a viewer. */
  reads?: Array<{ what: string } & Locator>;
}

function find(locator: Locator): HTMLElement | null {
  return 'label' in locator
    ? screen.queryByLabelText(locator.label)
    : screen.queryByTestId(locator.testId);
}

function renderCase(c: NodeCase, readOnly: boolean) {
  useCanvasCoreStore.setState({
    readOnly,
    nodes: [{ id: 'n1', type: c.type, data: c.data, position: { x: 0, y: 0 } }],
  });
  const View = SMART_NODE_TYPES[c.type] as ComponentType<Record<string, unknown>>;
  render(
    <Wrap>
      <View {...baseProps} id="n1" type={c.type} data={c.data} />
    </Wrap>,
  );
}

const OUTPUT_DATA = {
  kind: 'image',
  resource_id: '900',
  preview_text: 'out',
  preview_url: '/api/v1/resources/900/file',
  images: [{ url: '/api/v1/resources/900/file', name: 'out' }],
  gen_recover: ['task-1'],
  gen_slot: { node_id: 'p1' },
};

const CASES: NodeCase[] = [
  {
    name: 'character',
    type: 'character',
    data: { name: 'Ada', role_tag: 'lead', description: 'engineer', portrait_url: '' },
    writes: [
      { what: 'name', label: 'Character name' },
      { what: 'description', label: 'Character description' },
    ],
  },
  {
    name: 'location',
    type: 'location',
    data: { name: 'Dock', badge_tag: '', description: 'foggy', cover_url: '' },
    writes: [
      { what: 'name', label: 'Location name' },
      { what: 'description', label: 'Location description' },
    ],
  },
  {
    name: 'prop',
    type: 'prop',
    data: { name: 'Lamp', badge_tag: '', description: 'brass', cover_url: '' },
    writes: [
      { what: 'name', label: 'Prop name' },
      { what: 'description', label: 'Prop description' },
    ],
  },
  {
    name: 'shot (unbound)',
    type: 'shot',
    data: { title: 'Wide', reference_resource_ids: [], notes: 'n' },
    writes: [
      { what: 'title', label: 'Shot title' },
      { what: 'notes', label: 'Shot notes' },
    ],
  },
  {
    name: 'shot (bound to a script_shots row)',
    type: 'shot',
    data: {
      title: 'Wide',
      reference_resource_ids: [],
      notes: '',
      shot_id: '77',
      shot_label: 'S1',
      shot_type: null,
      camera_angle: null,
      camera_movement: null,
      focal_length: null,
      description: 'a dock at dawn',
      image_url: null,
      shot_status: null,
    },
    writes: [
      { what: 'shot type chip', label: 'Shot type' },
      { what: 'camera angle chip', label: 'Camera angle' },
      { what: 'camera movement chip', label: 'Camera movement' },
      { what: 'focal length chip', label: 'Focal length' },
      { what: 'description', label: 'Shot description' },
      { what: 'Generate', testId: 'shot-node-generate' },
    ],
  },
  {
    name: 'media (empty)',
    type: 'media',
    data: { title: 'Media', items: [] },
    writes: [{ what: 'upload drop zone', testId: 'media-node-empty' }],
  },
  {
    name: 'media (with items)',
    type: 'media',
    data: {
      title: 'Media',
      items: [{ url: '/api/v1/generated-media/1.png', kind: 'image', name: 'a' }],
    },
    writes: [{ what: 'Add', testId: 'media-node-add' }],
    reads: [{ what: 'thumbnail (opens the lightbox)', testId: 'media-node-thumb-0' }],
  },
  {
    name: 'prompt',
    type: 'prompt',
    data: {
      body: 'a robot',
      provider_slug: '',
      agent_id: null,
      run_status: 'failed',
      run_error: 'rate limited',
      run_started_at: null,
      run_finished_at: null,
      resource_refs: [
        { resource_id: 'r1', name: 'clip', kind: 'video', mime: '', scope: 'user' },
      ],
      negative_body: '',
      gen: null,
    },
    writes: [
      { what: 'body', label: 'Prompt body' },
      { what: 'negative body', label: 'Negative prompt' },
      { what: 'kind picker', label: 'Prompt kind' },
      { what: 'provider picker', label: 'Prompt provider' },
      { what: 'agent picker', label: 'Prompt agent' },
      { what: 'library loader', label: 'Load from library' },
      { what: 'ref chip remove', label: 'Remove reference to clip' },
      { what: 'Retry', testId: 'prompt-retry' },
    ],
  },
  {
    name: 'prompt (image generation)',
    type: 'prompt',
    data: {
      body: 'a robot',
      provider_slug: '',
      agent_id: null,
      run_status: 'idle',
      run_error: null,
      run_started_at: null,
      run_finished_at: null,
      resource_refs: [],
      gen: { kind: 'image', model: '', ratio: '1:1', count: 1 },
    },
    writes: [
      { what: 'generation model', label: 'Generation model' },
      { what: 'aspect ratio', label: 'Aspect ratio' },
      { what: 'image count', label: 'Image count' },
    ],
  },
  {
    name: 'llm',
    type: 'llm',
    data: {
      provider_slug: '',
      agent_id: null,
      input_text: 'hello',
      output_text: 'world',
      run_status: 'idle',
      run_error: null,
    },
    writes: [
      { what: 'provider picker', label: 'LLM provider' },
      { what: 'agent picker', label: 'LLM agent' },
      { what: 'input', label: 'LLM input' },
      { what: 'Run', testId: 'llm-run' },
    ],
    reads: [{ what: 'Copy output', label: 'Copy output' }],
  },
  {
    name: 'group',
    type: 'group',
    data: { label: 'Refs', items: [], uploading: 0 },
    writes: [{ what: 'label', label: 'Group label' }],
  },
  {
    name: 'timeline',
    type: 'timeline',
    data: {
      segments: [
        { id: 's1', prompt: 'open on a dock', seconds: 4 },
        { id: 's2', prompt: 'pan right', seconds: 3 },
      ],
      aspect: '',
      run_status: 'idle',
      segment_thumbs: [],
    },
    writes: [
      { what: 'aspect picker', label: 'Aspect ratio' },
      { what: 'add segment', label: 'Add segment' },
      { what: 'remove segment', label: 'Remove segment' },
      { what: 'segment seconds', label: 'Segment seconds' },
      { what: 'segment prompt', label: 'Segment prompt' },
      { what: 'resize grip', testId: 'timeline-resize-s1' },
      { what: 'Run', testId: 'timeline-run' },
    ],
    reads: [{ what: 'segment block (select to inspect)', testId: 'timeline-seg-s1' }],
  },
  {
    name: 'loop',
    type: 'loop',
    data: {
      mode: 'serial',
      label: '',
      show_prompt: true,
      image_input: true,
      image_batch_size: 1,
      rounds: 1,
      round_start: 1,
      prompts: ['a', 'b'],
    },
    writes: [
      { what: 'serial mode', label: 'Serial' },
      { what: 'parallel mode', label: 'Parallel' },
      { what: 'image input toggle', label: 'Toggle image input' },
      { what: 'prompt input toggle', label: 'Toggle prompt input' },
      { what: 'image batch size', label: 'canvas.loopBatch' },
      { what: 'prompt text', label: 'Loop prompt 1' },
      { what: 'remove prompt', label: 'Remove prompt 1' },
      { what: 'insert count token', label: 'Insert count token' },
      { what: 'add prompt', label: 'Add prompt' },
      { what: 'round start', label: 'canvas.loopStart' },
      { what: 'rounds', label: 'canvas.loopRounds' },
      { what: 'Run', testId: 'loop-run' },
    ],
  },
  {
    name: 'output',
    type: 'output',
    data: OUTPUT_DATA,
    writes: [
      { what: 'Rerun', testId: 'regenerate-open' },
      { what: 'Crop', testId: 'crop-open' },
      { what: 'Expand', testId: 'outpaint-open' },
      { what: 'Mask', testId: 'mask-cutout-open' },
      { what: 'Split', testId: 'grid-split-open' },
      { what: 'toolbar Rerun', label: 'Rerun' },
      { what: 'recover re-query', testId: 'output-recover-query' },
    ],
    reads: [
      { what: 'toolbar Preview', label: 'Preview' },
      { what: 'toolbar Download', label: 'Download' },
    ],
  },
];

describe('smart nodes — no dead write buttons in a read-only session', () => {
  describe.each(CASES)('$name', (c) => {
    it.each(c.writes)('withholds $what when read-only', (affordance) => {
      renderCase(c, true);
      const el = find(affordance);
      // Either shape is a valid answer to the click BEFORE it happens:
      // greyed out (kept for discoverability) or withdrawn entirely.
      if (el !== null) expect(el).toBeDisabled();
    });

    it.each(c.writes)('offers $what when writable', (affordance) => {
      renderCase(c, false);
      const el = find(affordance);
      expect(el).not.toBeNull();
      expect(el).not.toBeDisabled();
    });

    const reads = c.reads ?? [];
    if (reads.length > 0) {
      it.each(reads)('keeps $what usable when read-only', (affordance) => {
        renderCase(c, true);
        const el = find(affordance);
        expect(el).not.toBeNull();
        expect(el).not.toBeDisabled();
      });
    }
  });

  it('covers every type registered in SMART_NODE_TYPES', () => {
    // The registry — not this file's imagination — decides the inventory.
    // A new smart node type fails here until someone has actually looked at
    // its write affordances.
    const covered = new Set(CASES.map((c) => c.type));
    expect([...Object.keys(SMART_NODE_TYPES)].sort()).toEqual(
      [...covered].sort(),
    );
  });
});
