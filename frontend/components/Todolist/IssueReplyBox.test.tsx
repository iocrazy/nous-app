import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { IssueReplyBox } from './IssueReplyBox';
import { MAX_ASSET_REF_ATTACHMENTS, MAX_OUTPUT_REF_ATTACHMENTS } from '../chat/attachmentLimits';

// Mock the upload service to avoid hitting the network.
vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: {
    uploadChatAttachment: vi.fn().mockResolvedValue({
      kind: 'image',
      url: 'personal/u1/temp/x.png',
      filename: 'x.png',
      size_bytes: 100,
      mime: 'image/png',
      resource_id: 'res-1',
      file_path: 'personal/u1/temp/x.png',
    }),
  },
}));

// One shared spy, not a fresh `vi.fn()` per render: the cap refusal below has
// to assert what the composer SAID, and a spy the test cannot reach makes
// "it told the user" unprovable.
const addToast = vi.fn();
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));

import en from '../../public/locales/en.json';

/**
 * The i18n mock resolves against the REAL `en.json` rather than echoing the
 * key or the inline defaultValue. A mock that returns the fallback proves the
 * component ASKED for a string; it says nothing about whether the string
 * exists, so the cap assertion below would stay green with the copy deleted
 * (review M6). Resolving through the locale makes a missing
 * key a failing test.
 */
const lookup = (key: string): string | undefined =>
  key.split('.').reduce<unknown>(
    (node, part) =>
      node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined,
    en as unknown,
  ) as string | undefined;

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    // The real three-arg shape: `t(key)`, `t(key, options)` and
    // `t(key, defaultValue, options)`. The locale hit WINS over the inline
    // default — that ordering is the whole point.
    t: (k: string, arg2?: unknown, arg3?: unknown) => {
      const opts = (typeof arg2 === 'object' ? arg2 : arg3) as
        | Record<string, unknown>
        | undefined;
      const hit = lookup(k);
      const template =
        typeof hit === 'string' ? hit : typeof arg2 === 'string' ? arg2 : k;
      let out = template;
      for (const [name, value] of Object.entries(opts ?? {})) {
        out = out.split(`{{${name}}}`).join(String(value));
      }
      return out;
    },
  }),
}));


// The @-mention resource picker hook fires a debounced network search on
// mount (useResourceSearch → searchResources). Stub it so jsdom never hits
// the network and the component renders deterministically.
vi.mock('../../services/resourceSearchService', () => ({
  searchResources: vi.fn().mockResolvedValue({
    results: [],
    counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
    next_cursor: null,
  }),
}));

// --- Controllable fake tiptap editor ----------------------------------------
// Task 5 rewrote the composer from a <textarea> to a tiptap `useEditor`
// editor. Driving a real tiptap editor in jsdom is unreliable (no layout,
// contenteditable quirks), so we mock `@tiptap/react`'s `useEditor` and let
// each test set what `getText()` returns and what resourceRef nodes
// `descendants()` yields. The mock provides EVERY method/property the
// component touches during render + submit (getText, commands.clearContent /
// focus / insertResourceRef, setEditable, state.selection.from,
// state.doc.textBetween, state.doc.descendants, on, off) — otherwise the
// component throws on render.
let editorText = '';
let editorRefNodes: Array<{
  resourceId: string;
  name: string;
  mime: string;
  scope: { type: string; id: string };
}> = [];

/** The options the component hands `useEditor`. Captured so a test can drive
 *  the REAL `handleKeyDown` — "@" opens the mention picker, and with
 *  `EditorContent` mocked away there is no other way to reach that production
 *  code. Asserting against a re-implementation of it here would prove nothing
 *  about the component. */
let editorOptions: {
  editorProps?: { handleKeyDown?: (view: unknown, e: KeyboardEvent) => boolean };
} = {};

vi.mock('@tiptap/react', () => ({
  useEditor: (opts: unknown) => ((editorOptions = opts as typeof editorOptions), {
    getText: () => editorText,
    commands: {
      clearContent: vi.fn(),
      focus: vi.fn(),
      insertResourceRef: vi.fn(),
    },
    setEditable: vi.fn(),
    state: {
      selection: { from: 0 },
      doc: {
        textBetween: () => '',
        descendants: (cb: (n: unknown) => void) => {
          for (const r of editorRefNodes) {
            cb({ type: { name: 'resourceRef' }, attrs: r });
          }
        },
      },
    },
    on: vi.fn(),
    off: vi.fn(),
    chain: () => ({ focus: () => ({ run: () => {} }) }),
  }),
  EditorContent: () => null,
}));

/** `GET /api/v1/assets/search` rows — Envelope-unwrapped, every id a STRING
 *  (`assets_repository._serialize` calls `str()` on every BIGINT column), and
 *  `scope_id` null on a system preset. Copied from the real fixture rather
 *  than tidied. */
const AVA = {
  id: '727145299382534201',
  name: 'Ava',
  asset_type: 'character' as const,
  cover_file_id: '727145299382534301',
  readiness: { state: 'ready' as const, missing: [] },
  scope_id: '727145299382534200',
};

/**
 * `GET /api/v1/issues/{id}/outputs` — the REAL wire shape (3a Task 3): grouped
 * by object, `versions` newest first, every id a STRING (`run_deliverables.id`
 * / `run_id` / `ref_id` are Snowflake BIGINTs the router stringifies on
 * purpose), and `title` nullable.
 */
const listIssueOutputs = vi.fn();
vi.mock('../../services/outputsService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/outputsService')>();
  return { ...actual, listIssueOutputs: (...args: unknown[]) => listIssueOutputs(...args) };
});

const outputVersion = (v: number, over: Record<string, unknown> = {}) => ({
  id: `7271452993825349${10 + v}`,
  version: v,
  parent_version: v > 1 ? v - 1 : null,
  run_id: '727145299382534100',
  issue_id: '727145299382534000',
  seq: null, turn: null, step: v,
  title: 'S3 · Shot #1',
  model: null, cost_cents: null,
  created_at: '2026-09-10T00:00:00Z',
  ...over,
});

const SHOT_OUTPUT = {
  kind: 'script_shot',
  ref_id: '727145299382534999',
  title: 'S3 · Shot #1',
  latest_version: 2,
  versions: [outputVersion(2), outputVersion(1)],
};

const searchAssetsAccessible = vi.fn();
vi.mock('../../services/assetsService', () => ({
  searchAssetsAccessible: (...args: unknown[]) => searchAssetsAccessible(...args),
  // The staged chip's loadout menu fetches this on open. Never called in
  // these tests, but the module must export it or the import throws.
  fetchAssetDetail: vi.fn().mockResolvedValue({ loadouts: [] }),
}));

const _agents = [{ id: 'a1', slug: 'agent-1', name: 'Agent 1' }];

describe('IssueReplyBox', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    addToast.mockClear();
    editorText = '';
    editorRefNodes = [];
    listIssueOutputs.mockResolvedValue([SHOT_OUTPUT]);
  });

  it('renders the attachment picker (paperclip button) when no chips', () => {
    render(
      <IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} />,
    );
    // The picker renders a button with the Paperclip icon; we can find it by title or role.
    // The picker uses i18n key 'chat.attachments.attachTooltip' — accept either the key OR an English fallback.
    expect(
      screen.getByRole('button', { name: /attach|paperclip/i }),
    ).toBeInTheDocument();
  });

  it('passes attachments[] to onSubmit when send fires', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <IssueReplyBox agents={_agents as never} defaultAgentId="a1" onSubmit={onSubmit} />,
    );

    // Editor body text (mocked editor — see top of file)
    editorText = 'hello';

    // Stage a file through the attachment picker's hidden <input type=file>.
    // The paste path can't be driven now that EditorContent is mocked away
    // (tiptap's handlePaste lives on the real editor DOM, which no longer
    // renders), so we exercise the SAME upload pipeline via the picker — it
    // funnels through useChatAttachmentUpload → uploadChatAttachment exactly
    // like paste/drag does. This honestly proves the chip → staged → merged
    // into onSubmit's third arg contract.
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['data'], 'x.png', { type: 'image/png' });
    fireEvent.change(fileInput, { target: { files: [file] } });

    // Wait for the upload mock to resolve and the chip to appear
    await waitFor(() => {
      expect(screen.getByText(/x\.png/)).toBeInTheDocument();
    });

    // Click Send — find the submit button (Send icon button, aria-label="send")
    const sendBtn = container.querySelector('button[type="submit"], button[aria-label*="send" i]')!;
    fireEvent.click(sendBtn);

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
      const args = onSubmit.mock.calls[0];
      expect(args[0]).toBe('hello');            // body
      expect(args[1]).toBe('a1');                // agentId
      const atts = args[2];
      expect(Array.isArray(atts)).toBe(true);
      expect(atts).toHaveLength(1);
      expect(atts[0].kind).toBe('image');
      expect(atts[0].url).toBe('personal/u1/temp/x.png');
    });
  });

  it('shows the drop-files overlay on drag enter', () => {
    const { container } = render(
      <IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} />,
    );
    const wrapper = container.firstElementChild! as HTMLElement;
    fireEvent.dragEnter(wrapper);
    // Accept either the resolved English string OR the raw i18n key (no
    // i18next instance is initialized in unit tests, so useTranslation
    // returns the key verbatim).
    expect(
      screen.getByText(/Drop files to attach|chat\.attachments\.dropToUpload/i),
    ).toBeInTheDocument();
  });

  it('clears chips after a successful send', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <IssueReplyBox agents={_agents as never} defaultAgentId="a1" onSubmit={onSubmit} />,
    );
    editorText = 'msg';

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['data'], 'x.png', { type: 'image/png' });
    fireEvent.change(fileInput, { target: { files: [file] } });
    await waitFor(() => expect(screen.getByText(/x\.png/)).toBeInTheDocument());

    const sendBtn = container.querySelector('button[type="submit"], button[aria-label*="send" i]')!;
    fireEvent.click(sendBtn);
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText(/x\.png/)).not.toBeInTheDocument());
  });

  it('emits a resource_ref attachment when a resource is referenced', async () => {
    editorText = 'see this';
    editorRefNodes = [
      { resourceId: '900', name: 'demo.mp4', mime: 'video/mp4', scope: { type: 'team', id: 't1' } },
    ];
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <IssueReplyBox agents={_agents as never} defaultAgentId="a1" teamId="t1" onSubmit={onSubmit} />,
    );
    const sendBtn = container.querySelector('button[type="submit"], button[aria-label*="send" i]')!;
    fireEvent.click(sendBtn);
    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalled();
      const atts = onSubmit.mock.calls[0][2];
      const ref = atts.find((a: { kind: string }) => a.kind === 'resource_ref');
      expect(ref).toMatchObject({ kind: 'resource_ref', resource_id: '900', name: 'demo.mp4' });
    });
  });

  // ── Comment trigger disclosure + suppression ────────────────────────────
  describe('trigger chip', () => {
    const WAKES = { will_wake: true, agent_id: 'a1' };

    const send = (container: HTMLElement) =>
      fireEvent.click(
        container.querySelector('button[type="submit"], button[aria-label*="send" i]')!,
      );

    it('discloses the wake once there is something to send', () => {
      editorText = 'change the opening';
      render(
        <IssueReplyBox
          agents={_agents as never}
          onSubmit={vi.fn()}
          triggerPreview={WAKES}
          triggerAgentName="Agent 1"
        />,
      );
      expect(screen.getByTestId('comment-trigger-chip')).toHaveTextContent(
        /Will start when sent/i,
      );
    });

    it('stays quiet when the server says nothing wakes', () => {
      editorText = 'a note to self';
      render(
        <IssueReplyBox
          agents={_agents as never}
          onSubmit={vi.fn()}
          triggerPreview={{ will_wake: false, agent_id: null }}
        />,
      );
      expect(screen.queryByTestId('comment-trigger-chip')).toBeNull();
    });

    it('sends no suppress key when the chip is untouched', async () => {
      editorText = 'go ahead';
      const onSubmit = vi.fn().mockResolvedValue(undefined);
      const { container } = render(
        <IssueReplyBox
          agents={_agents as never}
          onSubmit={onSubmit}
          triggerPreview={WAKES}
        />,
      );
      send(container);
      await waitFor(() => expect(onSubmit).toHaveBeenCalled());
      expect(onSubmit.mock.calls[0][3]).toBeUndefined();
    });

    it('names the skipped agent in the payload once suppressed', async () => {
      editorText = 'wait for the client';
      const onSubmit = vi.fn().mockResolvedValue(undefined);
      const { container } = render(
        <IssueReplyBox
          agents={_agents as never}
          onSubmit={onSubmit}
          triggerPreview={WAKES}
        />,
      );
      fireEvent.click(screen.getByTestId('comment-trigger-chip'));
      send(container);
      await waitFor(() => expect(onSubmit).toHaveBeenCalled());
      expect(onSubmit.mock.calls[0][3]).toEqual(['a1']);
    });

    it('re-arms after sending — suppression is one comment, not a mode', async () => {
      editorText = 'quiet note';
      const onSubmit = vi.fn().mockResolvedValue(undefined);
      const { container } = render(
        <IssueReplyBox
          agents={_agents as never}
          onSubmit={onSubmit}
          triggerPreview={WAKES}
        />,
      );
      fireEvent.click(screen.getByTestId('comment-trigger-chip'));
      send(container);
      await waitFor(() => expect(onSubmit).toHaveBeenCalled());
      await waitFor(() =>
        expect(screen.getByTestId('comment-trigger-chip')).toHaveAttribute(
          'aria-pressed',
          'false',
        ),
      );
    });

    it('renders the quiet-note state when the server says the draft is a note', () => {
      editorText = '/note remember to check the license';
      render(
        <IssueReplyBox
          agents={_agents as never}
          onSubmit={vi.fn()}
          triggerPreview={{ will_wake: false, agent_id: 'a1', is_note: true }}
          triggerAgentName="Agent 1"
        />,
      );
      const chip = screen.getByTestId('comment-trigger-chip');
      expect(chip).toHaveTextContent(/Quiet note/i);
      expect(chip).toHaveTextContent(/won't wake/i);
    });

    it('reports the note boundary to the parent so it can refetch the preview', () => {
      // The mocked editor fires no update events, but the sync effect runs on
      // mount — mounting with a /note draft must cross the boundary exactly once.
      editorText = '/note quiet observation';
      const onNoteBoundaryChange = vi.fn();
      render(
        <IssueReplyBox
          agents={_agents as never}
          onSubmit={vi.fn()}
          triggerPreview={WAKES}
          onNoteBoundaryChange={onNoteBoundaryChange}
        />,
      );
      expect(onNoteBoundaryChange).toHaveBeenCalledWith('/note quiet observation');
    });

    it('does not report a boundary for a normal draft', () => {
      editorText = 'just a normal comment';
      const onNoteBoundaryChange = vi.fn();
      render(
        <IssueReplyBox
          agents={_agents as never}
          onSubmit={vi.fn()}
          triggerPreview={WAKES}
          onNoteBoundaryChange={onNoteBoundaryChange}
        />,
      );
      expect(onNoteBoundaryChange).not.toHaveBeenCalled();
    });

    it('does not report a boundary for a /notex draft (mirror of the backend rule)', () => {
      editorText = '/notex this is a comment';
      const onNoteBoundaryChange = vi.fn();
      render(
        <IssueReplyBox
          agents={_agents as never}
          onSubmit={vi.fn()}
          triggerPreview={WAKES}
          onNoteBoundaryChange={onNoteBoundaryChange}
        />,
      );
      expect(onNoteBoundaryChange).not.toHaveBeenCalled();
    });

    it('re-arms when the assignee changes under a pending suppression', async () => {
      // The user skipped agent a1. If the issue is reassigned to a2 before they
      // send, that skip must not silently swallow a2's run — a2 is an agent they
      // never saw the chip for.
      editorText = 'hold on';
      const onSubmit = vi.fn().mockResolvedValue(undefined);
      const { container, rerender } = render(
        <IssueReplyBox
          agents={_agents as never}
          onSubmit={onSubmit}
          triggerPreview={WAKES}
        />,
      );
      fireEvent.click(screen.getByTestId('comment-trigger-chip'));
      expect(screen.getByTestId('comment-trigger-chip')).toHaveAttribute(
        'aria-pressed',
        'true',
      );

      rerender(
        <IssueReplyBox
          agents={_agents as never}
          onSubmit={onSubmit}
          triggerPreview={{ will_wake: true, agent_id: 'a2' }}
        />,
      );
      expect(screen.getByTestId('comment-trigger-chip')).toHaveAttribute(
        'aria-pressed',
        'false',
      );

      send(container);
      await waitFor(() => expect(onSubmit).toHaveBeenCalled());
      expect(onSubmit.mock.calls[0][3]).toBeUndefined();
    });
  });
});

/**
 * The Assets tab in the issue reply box (v2 Task 3).
 *
 * Ruling H deferred asset references here while the chat panel got them, so
 * mentioning a character in an issue silently did nothing. The backend never
 * needed a change: `IssueMessagePost.attachments` is already
 * `List[AttachmentRequest]`, and `run_issue_reply_step` rebuilds them and
 * calls the SAME `run_session_turn` the chat router does — asset refs are
 * resolved (and capped) in `_run_session_turn_inner`, which both funnel
 * through. What was missing was entirely on this side.
 *
 * The wire shape is the assertion that matters: `{kind, asset_id, loadout_id,
 * name, mime, url}` and nothing else, with STRING ids. A field added or
 * renamed in passing shows up here as a failing equality rather than as a
 * reference the backend quietly fails to resolve.
 */
describe('IssueReplyBox — the Assets tab', () => {
  beforeEach(() => {
    searchAssetsAccessible.mockReset();
    searchAssetsAccessible.mockResolvedValue([AVA]);
    listIssueOutputs.mockReset();
    listIssueOutputs.mockResolvedValue([SHOT_OUTPUT]);
    addToast.mockClear();
    editorText = '';
    // Leaks from the first describe otherwise: a resourceRef node left over
    // from an earlier test rides along in the attachment payload and the
    // citation assertion reads as a mapper bug.
    editorRefNodes = [];
    editorOptions = {};
  });

  /** Type "@" through the component's own key handler and let the picker open. */
  async function openMentionPicker(): Promise<void> {
    await act(async () => {
      editorOptions.editorProps?.handleKeyDown?.(null, {
        key: '@',
        metaKey: false,
        ctrlKey: false,
        preventDefault: () => {},
      } as unknown as KeyboardEvent);
      // The handler defers the open by a tick so the character inserts first.
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  async function stageAva(): Promise<void> {
    await openMentionPicker();
    fireEvent.click(await screen.findByTestId('resource-picker-tab-assets'));
    fireEvent.mouseDown((await screen.findAllByTestId('mention-asset-option'))[0]);
  }

  it('offers the Assets tab beside the five resource kinds', async () => {
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} />);
    await openMentionPicker();
    expect(await screen.findByTestId('resource-picker-tab-assets')).toBeInTheDocument();
  });

  it('stages a chip when a row is picked, instead of inserting a node', async () => {
    // An asset is a turn-level attachment, not a span of the sentence — the
    // same reading the chat panel takes, so the two entry points cannot
    // disagree about what "mentioning a character" attached.
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} />);
    await stageAva();

    const chip = await screen.findByTestId('staged-asset-chip');
    expect(chip).toBeInTheDocument();
    expect(chip.getAttribute('data-asset-id')).toBe('727145299382534201');
    expect(screen.getByText('Ava')).toBeInTheDocument();
  });

  it('sends the asset as an asset_ref attachment in the real wire shape', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <IssueReplyBox agents={_agents as never} defaultAgentId="a1" onSubmit={onSubmit} />,
    );
    editorText = 'what is she wearing?';
    await stageAva();

    fireEvent.click(screen.getByRole('button', { name: 'send' }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    const [body, agentId, attachments] = onSubmit.mock.calls[0];
    expect(body).toBe('what is she wearing?');
    expect(agentId).toBe('a1');
    expect(attachments).toEqual([
      {
        kind: 'asset_ref',
        asset_id: '727145299382534201',
        loadout_id: null,
        name: 'Ava',
        mime: '',
        url: '',
      },
    ]);
  });

  it('keeps file attachments and resource refs alongside the asset', async () => {
    // Three sources, one list. An asset that displaced either of the other two
    // would be a regression nothing else in this file would notice.
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <IssueReplyBox agents={_agents as never} defaultAgentId="a1" onSubmit={onSubmit} />,
    );
    editorText = 'see these';
    editorRefNodes = [
      { resourceId: 'r1', name: 'clip.mp4', mime: 'video/mp4', scope: { type: 'personal', id: 'u' } },
    ];
    await stageAva();

    fireEvent.click(screen.getByRole('button', { name: 'send' }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    const kinds = (onSubmit.mock.calls[0][2] as Array<{ kind: string }>).map((a) => a.kind);
    expect(kinds).toEqual(['resource_ref', 'asset_ref']);
  });

  it('clears the staged assets after a successful send', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <IssueReplyBox agents={_agents as never} defaultAgentId="a1" onSubmit={onSubmit} />,
    );
    editorText = 'hi';
    await stageAva();

    fireEvent.click(screen.getByRole('button', { name: 'send' }));

    await waitFor(() => expect(screen.queryByTestId('staged-asset-chip')).toBeNull());
  });

  it('keeps the staged asset when the parent rejects, so the user can retry', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('offline'));
    render(
      <IssueReplyBox agents={_agents as never} defaultAgentId="a1" onSubmit={onSubmit} />,
    );
    editorText = 'hi';
    await stageAva();

    fireEvent.click(screen.getByRole('button', { name: 'send' }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(screen.getByTestId('staged-asset-chip')).toBeInTheDocument();
  });

  /** Press a bare key through the component's own editor key handler. */
  function press(key: string): boolean | undefined {
    return editorOptions.editorProps?.handleKeyDown?.(null, {
      key,
      metaKey: false,
      ctrlKey: false,
      preventDefault: () => {},
    } as unknown as KeyboardEvent);
  }

  it('routes the arrow keys into the Assets grid once that tab is open', async () => {
    // The same claim AIChatPanel makes, and the reason the picker handle is
    // held by a ref here at all: without this the tab would be mouse-only.
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} />);
    await openMentionPicker();
    fireEvent.click(await screen.findByTestId('resource-picker-tab-assets'));
    await screen.findAllByTestId('mention-asset-option');

    expect(press('ArrowDown')).toBe(true);
  });

  it('leaves the arrow keys to the editor while the Assets tab is closed', async () => {
    // The five resource tabs never moved their highlight with the arrows.
    // Claiming the key there would break ordinary cursor movement in a
    // multi-line reply for no gain.
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} />);
    await openMentionPicker();

    expect(press('ArrowDown')).toBeFalsy();
  });

  it('stages the highlighted asset on ↵, which is what the picker hint promises', async () => {
    // The grid highlights its first tile as soon as rows arrive, so ↵ has
    // something to commit — the same behaviour AIChatPanel gets from the same
    // handle, and what the picker's own "↵ insert" hint tells the user.
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} />);
    await openMentionPicker();
    fireEvent.click(await screen.findByTestId('resource-picker-tab-assets'));
    await screen.findAllByTestId('mention-asset-option');

    await act(async () => { press('Enter'); });

    expect(await screen.findByTestId('staged-asset-chip')).toBeInTheDocument();
  });

  it('leaves plain ↵ to the editor while the Assets tab is closed', async () => {
    // Issue replies are multi-line and send on ⌘↩, so a plain Enter the
    // picker did not claim has to reach the editor as a newline.
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} />);
    await openMentionPicker();

    expect(press('Enter')).toBeFalsy();
  });

  it('still sends on Cmd+Enter with the Assets tab open', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <IssueReplyBox agents={_agents as never} defaultAgentId="a1" onSubmit={onSubmit} />,
    );
    editorText = 'hi';
    await openMentionPicker();
    fireEvent.click(await screen.findByTestId('resource-picker-tab-assets'));
    await screen.findAllByTestId('mention-asset-option');

    await act(async () => {
      editorOptions.editorProps?.handleKeyDown?.(null, {
        key: 'Enter',
        metaKey: true,
        ctrlKey: false,
        preventDefault: () => {},
      } as unknown as KeyboardEvent);
    });

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
  });

  // ── Citations: the Outputs tab (harness 3a Task 6) ──────────────────────
  //
  // The tab exists only where there is an issue to be scoped to. That is not
  // a UI preference: `output_ref_resolver` validates that the cited version
  // was produced ON THIS ISSUE, so a composer with no issue behind it has
  // nothing to check against and the chat panel refuses the kind outright.

  const ISSUE = 727145299382534000;

  async function openOutputsTab(): Promise<void> {
    await openMentionPicker();
    fireEvent.click(await screen.findByTestId('resource-picker-tab-outputs'));
  }

  async function citeLatest(): Promise<void> {
    await openOutputsTab();
    fireEvent.click((await screen.findAllByTestId('output-picker-row'))[0]);
  }

  it('offers the Outputs tab only where an issue backs the composer', async () => {
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} issueId={ISSUE} />);
    await openMentionPicker();
    expect(await screen.findByTestId('resource-picker-tab-outputs')).toBeInTheDocument();
  });

  it('hides the Outputs tab when there is no issue to cite against', async () => {
    // Not cosmetic: with no issue every citation would come back
    // `output_ref_unresolvable`, so offering the tab would be offering a
    // control that cannot work.
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} />);
    await openMentionPicker();
    expect(await screen.findByTestId('resource-picker-tab-assets')).toBeInTheDocument();
    expect(screen.queryByTestId('resource-picker-tab-outputs')).toBeNull();
    expect(listIssueOutputs).not.toHaveBeenCalled();
  });

  it('reads this issue’s outputs, and only on demand', async () => {
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} issueId={ISSUE} />);
    await openMentionPicker();
    expect(listIssueOutputs).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId('resource-picker-tab-outputs'));
    await waitFor(() => expect(listIssueOutputs).toHaveBeenCalledWith(ISSUE));
  });

  it('stages a citation chip naming the object AND the version', async () => {
    // The version is the point. A chip reading "@S3 · Shot #1" alone would be
    // the same chip whichever revision the reader picked, and revising the
    // object later would silently re-point what the comment meant.
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} issueId={ISSUE} />);
    await citeLatest();
    const chip = await screen.findByTestId('staged-output-chip');
    expect(chip.textContent).toContain('S3 · Shot #1');
    expect(chip.textContent).toContain('v2');
    expect(chip.getAttribute('data-version')).toBe('2');
    expect(chip.getAttribute('data-ref')).toBe('727145299382534999');
  });

  it('lets one object be cited at two versions at once', async () => {
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} issueId={ISSUE} />);
    await citeLatest();
    await openOutputsTab();
    fireEvent.click((await screen.findAllByTestId('output-picker-row'))[1]);
    const chips = await screen.findAllByTestId('staged-output-chip');
    expect(chips.map((c) => c.getAttribute('data-version'))).toEqual(['2', '1']);
  });

  it('drops one citation without taking the other with it', async () => {
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} issueId={ISSUE} />);
    await citeLatest();
    await openOutputsTab();
    fireEvent.click((await screen.findAllByTestId('output-picker-row'))[1]);
    await screen.findAllByTestId('staged-output-chip');
    fireEvent.click(screen.getAllByTestId('staged-output-chip-remove')[0]);
    const left = await screen.findAllByTestId('staged-output-chip');
    expect(left).toHaveLength(1);
    expect(left[0].getAttribute('data-version')).toBe('1');
  });

  it('sends the citation as an output_ref attachment, version intact', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<IssueReplyBox agents={_agents as never} onSubmit={onSubmit} issueId={ISSUE} />);
    await citeLatest();
    await screen.findByTestId('staged-output-chip');
    editorText = 'this one changed';
    await act(async () => {
      editorOptions.editorProps?.handleKeyDown?.(null, {
        key: 'Enter', metaKey: true, ctrlKey: false, preventDefault: () => {},
      } as unknown as KeyboardEvent);
    });
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][2]).toEqual([
      {
        kind: 'output_ref',
        ref_kind: 'script_shot',
        ref_id: '727145299382534999',
        version: 2,
        title: 'S3 · Shot #1',
      },
    ]);
  });

  it('keeps the citations staged when the send is rejected', async () => {
    // The refusal path for citations is all-or-nothing — the comment did not
    // post. Clearing the chips would make the retry start from re-picking.
    const onSubmit = vi.fn().mockRejectedValue(new Error('nope'));
    render(<IssueReplyBox agents={_agents as never} onSubmit={onSubmit} issueId={ISSUE} />);
    await citeLatest();
    await screen.findByTestId('staged-output-chip');
    editorText = 'x';
    await act(async () => {
      editorOptions.editorProps?.handleKeyDown?.(null, {
        key: 'Enter', metaKey: true, ctrlKey: false, preventDefault: () => {},
      } as unknown as KeyboardEvent);
    });
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(screen.getAllByTestId('staged-output-chip')).toHaveLength(1);
  });

  it('leaves only one extra tab lit at a time', async () => {
    // Assets and Outputs are two different populations sharing one set of
    // arrow keys. Both lit would mean two bodies on screen and Enter picking
    // from whichever the code reached first.
    searchAssetsAccessible.mockResolvedValue([AVA]);
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} issueId={ISSUE} />);
    await openMentionPicker();
    fireEvent.click(await screen.findByTestId('resource-picker-tab-assets'));
    await waitFor(() =>
      expect(screen.getByTestId('resource-picker-tab-assets').getAttribute('aria-pressed')).toBe('true'));

    fireEvent.click(screen.getByTestId('resource-picker-tab-outputs'));
    await waitFor(() =>
      expect(screen.getByTestId('resource-picker-tab-outputs').getAttribute('aria-pressed')).toBe('true'));
    expect(screen.getByTestId('resource-picker-tab-assets').getAttribute('aria-pressed')).toBe('false');

    fireEvent.click(screen.getByTestId('resource-picker-tab-assets'));
    await waitFor(() =>
      expect(screen.getByTestId('resource-picker-tab-outputs').getAttribute('aria-pressed')).toBe('false'));
  });

  it('refuses the ninth citation OUT LOUD, naming the cap', async () => {
    // Past the cap the server refuses the WHOLE comment (`output_ref_limit_
    // exceeded`), and an issue reply runs asynchronously in a DBOS workflow
    // this composer never hears back from. A ninth chip that looked staged
    // would produce a comment the writer believes was sent and that never
    // posts.
    listIssueOutputs.mockResolvedValue(
      Array.from({ length: 9 }, (_, i) => ({
        kind: 'script_shot',
        ref_id: `72714529938253${4900 + i}`,
        title: `Shot #${i}`,
        latest_version: 1,
        versions: [outputVersion(1, { title: `Shot #${i}` })],
      })),
    );
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} issueId={ISSUE} />);

    for (let i = 0; i < MAX_OUTPUT_REF_ATTACHMENTS; i += 1) {
      await openOutputsTab();
      fireEvent.click((await screen.findAllByTestId('output-picker-row'))[i]);
    }
    expect(await screen.findAllByTestId('staged-output-chip')).toHaveLength(
      MAX_OUTPUT_REF_ATTACHMENTS,
    );

    await openOutputsTab();
    fireEvent.click((await screen.findAllByTestId('output-picker-row'))[8]);

    expect(screen.getAllByTestId('staged-output-chip')).toHaveLength(
      MAX_OUTPUT_REF_ATTACHMENTS,
    );
    // The SENTENCE, resolved from `en.json` with `{{n}}` filled in — not the
    // key, which would stay green with the copy deleted.
    expect(addToast).toHaveBeenCalledWith(
      `A comment can reference at most ${MAX_OUTPUT_REF_ATTACHMENTS} outputs`,
      'error',
    );
    expect(addToast.mock.calls[0][0]).not.toContain('{{');
    expect(addToast.mock.calls[0][0]).not.toContain('outputs.citationLimit');
  });

  it('says the read failed rather than showing an empty shelf', async () => {
    listIssueOutputs.mockRejectedValue(new Error('offline'));
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} issueId={ISSUE} />);
    await openOutputsTab();
    expect(await screen.findByTestId('output-picker-error')).toBeInTheDocument();
    expect(screen.queryByTestId('output-picker-empty')).toBeNull();
  });

  it('refuses the ninth asset OUT LOUD rather than letting the server drop it', async () => {
    // The server caps at MAX_ASSET_REF_ATTACHMENTS and reports the refusal in
    // `attachment_failures` — which this composer never sees, because the
    // issue turn runs asynchronously in a DBOS workflow. Without a check here
    // the ninth pick would be a silent no-op, which CLAUDE.md forbids for any
    // user-triggered path.
    const rows = Array.from({ length: 9 }, (_, i) => ({
      ...AVA,
      id: `72714529938253430${i}`,
      name: `Ava ${i}`,
    }));
    searchAssetsAccessible.mockResolvedValue(rows);
    render(<IssueReplyBox agents={_agents as never} onSubmit={vi.fn()} />);

    // Re-query the tiles every round: picking closes the picker, so the next
    // open re-mounts the grid and the previous nodes are detached.
    for (let i = 0; i < MAX_ASSET_REF_ATTACHMENTS; i += 1) {
      await openMentionPicker();
      fireEvent.click(await screen.findByTestId('resource-picker-tab-assets'));
      fireEvent.mouseDown((await screen.findAllByTestId('mention-asset-option'))[i]);
    }
    expect(await screen.findAllByTestId('staged-asset-chip')).toHaveLength(
      MAX_ASSET_REF_ATTACHMENTS,
    );

    await openMentionPicker();
    fireEvent.click(await screen.findByTestId('resource-picker-tab-assets'));
    fireEvent.mouseDown((await screen.findAllByTestId('mention-asset-option'))[8]);

    // Still eight — and the user was told why, not left to wonder.
    expect(screen.getAllByTestId('staged-asset-chip')).toHaveLength(
      MAX_ASSET_REF_ATTACHMENTS,
    );
    // The SENTENCE, resolved from `en.json` and with `{{n}}` interpolated —
    // not the key. A key assertion proves a toast fired; it stays green when
    // the copy is missing, which is the one thing this refusal must not do.
    expect(addToast).toHaveBeenCalledWith(
      `Only The First ${MAX_ASSET_REF_ATTACHMENTS} Assets Were Used`,
      'error',
    );
    expect(addToast.mock.calls[0][0]).not.toContain('{{');
    expect(addToast.mock.calls[0][0]).not.toContain('attachmentFailureReason');
  });
});
