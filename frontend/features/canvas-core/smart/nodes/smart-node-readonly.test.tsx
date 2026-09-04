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
 *   readOnly:false → present, enabled, editable  (editing still works)
 *   readOnly:true  → withheld                    (nothing lit that no-ops)
 *
 * "Withheld" has TWO correct shapes and the distinction is not cosmetic:
 *
 *   buttons / selects / toggles / steppers → `disabled`, or removed. There
 *     is no text in them worth copying, and `<select>` has no `readonly`
 *     semantics in HTML at all.
 *   free-text `<input>` / `<textarea>`     → `readonly`, NEVER `disabled`.
 *     A disabled field cannot be focused, selected or copied — and reading
 *     is precisely what a read-only viewer came to do. Disabling the shot
 *     description or the prompt body would take away the one thing they
 *     ARE entitled to. `readonly` is the HTML feature for exactly this:
 *     not editable, still focusable and selectable.
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

/**
 * `text: true` marks a free-text field — the ones that must degrade to
 * `readonly` rather than `disabled` so their content stays selectable.
 * Anything without it is a button / picker / stepper and takes `disabled`.
 */
/** `richText: true` marks a free-text field that is a contenteditable surface
 *  rather than a form control (the prompt body is a tiptap editor so it can
 *  hold inline image chips). The REQUIREMENT is identical — withheld without
 *  being disabled, still focusable, content still readable — only the
 *  attributes expressing it differ. */
type Affordance = { what: string; text?: true; richText?: true } & Locator;

interface NodeCase {
  /** Row name — also the `type` prop React Flow would pass. */
  name: string;
  /** Key into SMART_NODE_TYPES; several rows may share one renderer. */
  type: keyof typeof SMART_NODE_TYPES;
  data: Record<string, unknown>;
  /** Anything that writes the document or dispatches a run. */
  writes: Affordance[];
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
      {/* Selected, because that is how a user reaches the floating toolbar:
          since fluency T5 the bar is mounted only while the card is pinned
          (selected) or hovered, so an unselected render has no toolbar to
          make assertions about. */}
      <View {...baseProps} selected id="n1" type={c.type} data={c.data} />
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
    // The reference-file CHECKLIST is the asset card's other write
    // affordance, and it cannot appear here: this file renders the nodes
    // with no Router, so `useCanvasScope` answers an empty scope, the card
    // asks the assets API nothing (deliberately — an empty `scope_id` is a
    // 403, not an unscoped query) and there are no files to list. Its
    // read-only behaviour is pinned in `AssetNodeView.test.tsx`, which
    // renders inside a route and stubs the detail fetch.
    name: 'asset',
    type: 'asset',
    data: {
      asset_id: '727145299382534300',
      loadout_id: null,
      selected_file_ids: [],
      name: 'Cole Bannon',
      asset_type: 'character',
      cover_file_id: null,
      readiness_state: 'ready',
    },
    writes: [{ what: 'loadout picker', label: 'Asset loadout' }],
    reads: [{ what: 'Open Sheet link', testId: 'asset-node-open-sheet' }],
  },
  {
    name: 'character',
    type: 'character',
    data: { name: 'Ada', role_tag: 'lead', description: 'engineer', portrait_url: '' },
    writes: [
      { what: 'name', label: 'Character name', text: true },
      { what: 'description', label: 'Character description', text: true },
    ],
  },
  {
    name: 'location',
    type: 'location',
    data: { name: 'Dock', badge_tag: '', description: 'foggy', cover_url: '' },
    writes: [
      { what: 'name', label: 'Location name', text: true },
      { what: 'description', label: 'Location description', text: true },
    ],
  },
  {
    name: 'prop',
    type: 'prop',
    data: { name: 'Lamp', badge_tag: '', description: 'brass', cover_url: '' },
    writes: [
      { what: 'name', label: 'Prop name', text: true },
      { what: 'description', label: 'Prop description', text: true },
    ],
  },
  {
    name: 'shot (unbound)',
    type: 'shot',
    data: { title: 'Wide', reference_resource_ids: [], notes: 'n' },
    writes: [
      { what: 'title', label: 'Shot title', text: true },
      { what: 'notes', label: 'Shot notes', text: true },
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
      { what: 'description', label: 'Shot description', text: true },
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
      negative_body: 'blurry, watermark',
      gen: null,
    },
    writes: [
      { what: 'body', label: 'Prompt body', text: true, richText: true },
      { what: 'negative body', label: 'Negative prompt', text: true },
      { what: 'kind picker', label: 'Prompt kind' },
      { what: 'provider picker', label: 'Prompt provider' },
      { what: 'agent picker', label: 'Prompt agent' },
      { what: 'library loader', label: 'Prompt Templates' },
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
      { what: 'input', label: 'LLM input', text: true },
      { what: 'Run', testId: 'llm-run' },
    ],
    reads: [{ what: 'Copy output', label: 'Copy output' }],
  },
  {
    name: 'group',
    type: 'group',
    data: { label: 'Refs', items: [], uploading: 0 },
    writes: [{ what: 'label', label: 'Group label', text: true }],
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
      { what: 'segment prompt', label: 'Segment prompt', text: true },
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
      { what: 'prompt text', label: 'Loop prompt 1', text: true },
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
      // Editing actions live only on the floating toolbar now (the header
      // chip row was removed — IC single-toolbar look, 2026-08-21).
      { what: 'toolbar Crop', label: 'Crop' },
      { what: 'toolbar Expand', label: 'Expand' },
      { what: 'toolbar Mask', label: 'Mask' },
      { what: 'toolbar Split', label: 'Split' },
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
      if (affordance.richText) {
        // Same requirement, expressed the way a contenteditable does it.
        expect(el).not.toBeNull();
        expect(el).toHaveAttribute('contenteditable', 'false');
        expect(el).toHaveAttribute('aria-readonly', 'true');
        expect(el).not.toBeDisabled();
        return;
      }
      if (affordance.text) {
        // A text field is withheld by going `readonly` — and it must NOT
        // have gone `disabled`, which would cost the viewer the ability to
        // select and copy it.
        expect(el).not.toBeNull();
        expect(el).toHaveAttribute('readonly');
        expect(el).not.toBeDisabled();
        return;
      }
      // Everything else: greyed out (kept for discoverability) or
      // withdrawn entirely — both are an answer given before the click.
      if (el !== null) expect(el).toBeDisabled();
    });

    it.each(c.writes)('offers $what when writable', (affordance) => {
      renderCase(c, false);
      const el = find(affordance);
      expect(el).not.toBeNull();
      expect(el).not.toBeDisabled();
      if (affordance.text) expect(el).not.toHaveAttribute('readonly');
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

  // ---------------------------------------------------------------- //
  // Semantic regression fence: text stays readable.
  //
  // The first version of this fix reached for `disabled` on the text
  // fields too, "for consistency" with the existing `stale` handling.
  // `disabled` means "this control takes no part in interaction" — it
  // cannot be focused, and its content cannot be selected or copied.
  // Applied to a read-only session that is a straight downgrade: it takes
  // away READING from the one user whose entire session is reading. HTML
  // already has the right word for "not editable but still yours to read":
  // `readonly`. This block pins that choice so nobody trades it back.
  // ---------------------------------------------------------------- //
  const TEXT_FIELDS = CASES.flatMap((c) =>
    c.writes
      .filter((w) => w.text)
      .map((w) => ({ case: c, what: `${c.name} · ${w.what}`, locator: w })),
  );

  it.each(TEXT_FIELDS)(
    'keeps $what focusable and copyable when read-only',
    ({ case: c, locator }) => {
      renderCase(c, true);
      const el = find(locator) as HTMLInputElement | HTMLTextAreaElement | null;
      expect(el).not.toBeNull();

      // Never `disabled` — that is the regression this test exists for.
      expect(el).not.toBeDisabled();
      if (locator.richText) {
        expect(el).toHaveAttribute('aria-readonly', 'true');
      } else {
        expect(el).toHaveAttribute('readonly');
      }

      // Focusable, which is what makes select-and-copy possible at all.
      // A contenteditable=false div needs an explicit tabindex for this;
      // losing it would silently cost keyboard users the prompt text.
      el!.focus();
      expect(document.activeElement).toBe(el);

      // And the content is still THERE to be read — a field emptied or
      // replaced by a placeholder would pass every check above.
      const content = locator.richText ? (el!.textContent ?? '') : el!.value;
      expect(content.length).toBeGreaterThan(0);
    },
  );

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
