import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { IssueReplyBox } from './IssueReplyBox';

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

vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

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

vi.mock('@tiptap/react', () => ({
  useEditor: () => ({
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

const _agents = [{ id: 'a1', slug: 'agent-1', name: 'Agent 1' }];

describe('IssueReplyBox', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    editorText = '';
    editorRefNodes = [];
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
});
