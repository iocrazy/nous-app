/**
 * The whole point of the change, end to end in the real panel: an asset the
 * user picks lands in the attachment row ABOVE the composer instead of
 * becoming a node inside the sentence, and it still reaches the backend in
 * exactly the shape it always did.
 *
 * The unit suites cover the pieces (stagedResources.test.ts for the merge,
 * ChatAttachmentPicker.resources.test.tsx for the row, the hook test for the
 * two channels). This one covers the wiring between them, which is where a
 * prop passed to the wrong component would hide.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, def?: unknown) => (typeof def === 'string' ? def : key) }),
}));

const streamChatMessage = vi.fn();
const SESSION = { id: 'sess-1', title: 'New conversation', agent_slug: 'analyze' };

vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: {
    listAgents: vi.fn(async () => [
      { id: '1', slug: 'analyze', name: 'Analyze', enabled: true },
    ]),
    listChatSessions: vi.fn(async () => [SESSION]),
    listAllChatSessions: vi.fn(async () => []),
    getChatSession: vi.fn(async () => ({ ...SESSION, messages: [] })),
    createChatSession: vi.fn(async () => SESSION),
    updateChatSession: vi.fn(async () => SESSION),
    deleteChatSession: vi.fn(async () => undefined),
    streamChatMessage: (...args: unknown[]) => streamChatMessage(...args),
    uploadChatAttachment: vi.fn(),
  },
}));

// Search rows for the @ picker.
const SEARCH_ROW = {
  id: '339710259795355',
  name: 'pitch.mp4',
  kind: 'video',
  mime: 'video/mp4',
  size: 1024,
  scope: { type: 'personal' as const, id: 'u' },
  updated_at: '2026-08-18T00:00:00Z',
  thumbnail_url: null,
  transcript_status: 'completed',
  summary_status: 'completed',
};
vi.mock('../hooks/useResourceSearch', () => ({
  useResourceSearch: () => ({
    data: { results: [SEARCH_ROW], counts: {} },
    loading: false,
    error: null,
  }),
}));

// Attaching tops up AI processing (spec F1) — not what this suite is about.
vi.mock('../utils/ensureResourceProcessed', () => ({
  ensureResourceProcessed: vi.fn(async () => ({ action: 'ready' })),
}));
vi.mock('../hooks/useResourceProcessingFollowUps', () => ({
  useResourceProcessingFollowUps: () => undefined,
}));
vi.mock('../hooks/useOptionalTaskManager', () => ({
  useOptionalTaskManager: () => null,
}));

// The REAL ChatInput (and a real tiptap editor) stays mounted — this only
// grabs the editor ref the panel hands it, so a test can seed the "@query"
// the user would have typed. jsdom cannot type into contenteditable, and an
// assertion about text that was never entered proves nothing.
let composerEditor: { current: any } | null = null;
vi.mock('./chat/ChatInput', async (importOriginal) => {
  const mod = await importOriginal<typeof import('./chat/ChatInput')>();
  return {
    ...mod,
    ChatInput: (props: any) => {
      composerEditor = props.editorRef;
      return <mod.ChatInput {...props} />;
    },
  };
});

import { AIChatPanel } from './AIChatPanel';
import { ToastProvider } from './Toast';
import { useGlobalChatStore } from '../stores/globalChatStore';

/** What the library context menu hands the store on "Send to Agent". */
const PENDING = {
  resourceId: '339710259795355',
  name: 'pitch.mp4',
  kind: 'video' as const,
  mime: 'video/mp4',
  scope: { type: 'personal' as const, id: 'u' },
  thumbnailUrl: null,
  transcriptStatus: 'completed',
  summaryStatus: 'completed',
};

async function renderPanel() {
  const view = render(
    <ToastProvider>
      <AIChatPanel projectId="1" agentSlug="analyze" />
    </ToastProvider>,
  );
  // Composer is disabled until agent + session resolve.
  await waitFor(() => {
    expect(view.container.querySelector('[contenteditable="true"]')).not.toBeNull();
  });
  return view;
}

function attachmentRow(): HTMLElement {
  return screen.getByTestId('composer-attachment-row');
}

beforeEach(() => {
  vi.clearAllMocks();
  composerEditor = null;
  // jsdom has no layout. Both the message list's autoscroll and
  // prosemirror's scroll-caret-into-view walk the DOM for geometry, and a
  // throw there surfaces as an UNHANDLED error — vitest exits non-zero
  // while still printing "6 passed", so reading only the summary line lies.
  Element.prototype.scrollIntoView = vi.fn();
  (Node.prototype as unknown as { getClientRects: () => unknown }).getClientRects =
    () => Object.assign([], { item: () => null });
  (Node.prototype as unknown as { getBoundingClientRect: () => unknown }).getBoundingClientRect =
    () => ({ top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0, x: 0, y: 0 });
  // prosemirror measures the caret through a Range, which jsdom leaves
  // without geometry methods entirely.
  (Range.prototype as unknown as { getClientRects: () => unknown }).getClientRects =
    () => Object.assign([], { item: () => null });
  (Range.prototype as unknown as { getBoundingClientRect: () => unknown }).getBoundingClientRect =
    () => ({ top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0, x: 0, y: 0 });
  useGlobalChatStore.setState({ pendingResource: null, pendingQuote: null, chatRequest: null });
  // One `done` event and nothing else — enough for handleSend to complete.
  streamChatMessage.mockImplementation(async function* () {
    yield { type: 'done', data: {} };
  });
});

describe('a picked asset lands in the attachment row, not in the sentence', () => {
  it('stages the context menu\'s "Send to Agent" asset above the composer', async () => {
    await renderPanel();

    await waitFor(() => {
      useGlobalChatStore.getState().sendResourceToChat(PENDING);
    });

    await waitFor(() => {
      expect(within(attachmentRow()).getByTestId('staged-resource-chip')).toBeVisible();
    });
    expect(within(attachmentRow()).getByText('pitch.mp4')).toBeVisible();
    // The old behaviour, explicitly ruled out: no inline chip in the input.
    expect(screen.queryByTestId('resource-chip')).toBeNull();
  });

  it('leaves the caret in the composer after Send to Agent', async () => {
    // The user came from the library context menu, so nothing has put a
    // caret here. Without it they must click the input before typing —
    // the old inline-insert path focused as a side effect of inserting.
    const { container } = await renderPanel();
    await waitFor(() => {
      useGlobalChatStore.getState().sendResourceToChat(PENDING);
    });
    await waitFor(() => {
      expect(screen.getByTestId('staged-resource-chip')).toBeVisible();
    });

    const editable = container.querySelector('[contenteditable="true"]');
    await waitFor(() => {
      expect(document.activeElement).toBe(editable);
    });
  });

  it('stages an @-picked asset and clears the "@query" the user typed', async () => {
    await renderPanel();
    await waitFor(() => {
      expect(composerEditor?.current).toBeTruthy();
    });
    const editor = composerEditor!.current;

    // What the user actually did: typed "look at @pit" and waited for the
    // picker. The insert fires tiptap's update handler, which is what opens
    // it — same path as real typing.
    // The space goes in as content, not as HTML — setContent would collapse
    // a trailing one and the assertion below would be measuring the wrong doc.
    editor.commands.setContent('<p>look at</p>');
    editor.commands.focus('end');
    editor.commands.insertContent(' @pit');
    await waitFor(() => {
      expect(screen.getByTestId('resource-picker')).toBeVisible();
    });
    expect(editor.getText()).toBe('look at @pit');

    fireEvent.click(screen.getAllByTestId('resource-picker-row')[0]);

    await waitFor(() => {
      expect(within(attachmentRow()).getByTestId('staged-resource-chip')).toBeVisible();
    });
    expect(screen.queryByTestId('resource-chip')).toBeNull();
    // Leftover "@pit" would otherwise be sent as message body.
    expect(editor.getText()).toBe('look at ');
  });
});

describe('sending', () => {
  it('sends the staged asset in the unchanged resource_ref wire shape', async () => {
    await renderPanel();
    await waitFor(() => {
      useGlobalChatStore.getState().sendResourceToChat(PENDING);
    });
    await waitFor(() => {
      expect(screen.getByTestId('staged-resource-chip')).toBeVisible();
    });

    fireEvent.click(screen.getByTitle('Send message'));

    await waitFor(() => {
      expect(streamChatMessage).toHaveBeenCalled();
    });
    const [, , opts] = streamChatMessage.mock.calls[0] as [string, string, any];
    expect(opts.attachments).toEqual([
      {
        kind: 'resource_ref',
        url: '',                       // resource_ref resolves by id, not URL
        resource_id: '339710259795355',
        mime: 'video/mp4',
        alt_text: 'pitch.mp4',
      },
    ]);
  });

  it('sends an asset-only turn, with no text typed', async () => {
    // The send guard used to require text OR an inline chip. With chips out
    // of the doc, "here, look at this" had no way through.
    await renderPanel();
    await waitFor(() => {
      useGlobalChatStore.getState().sendResourceToChat(PENDING);
    });
    await waitFor(() => {
      expect(screen.getByTestId('staged-resource-chip')).toBeVisible();
    });

    fireEvent.click(screen.getByTitle('Send message'));

    await waitFor(() => {
      expect(streamChatMessage).toHaveBeenCalled();
    });
    const [, text] = streamChatMessage.mock.calls[0] as [string, string, unknown];
    expect(text).toBe('');
  });

  it('clears the attachment row once the turn is sent', async () => {
    await renderPanel();
    await waitFor(() => {
      useGlobalChatStore.getState().sendResourceToChat(PENDING);
    });
    await waitFor(() => {
      expect(screen.getByTestId('staged-resource-chip')).toBeVisible();
    });

    fireEvent.click(screen.getByTitle('Send message'));

    await waitFor(() => {
      expect(screen.queryByTestId('staged-resource-chip')).toBeNull();
    });
  });

  it('keeps the asset staged when the turn never reached the server', async () => {
    // Losing the chip AND the message would make the user re-pick from the
    // library with nothing on screen saying why.
    streamChatMessage.mockImplementation(async function* () {
      yield { type: 'error', data: { error: 'connection reset' } };
    });
    await renderPanel();
    await waitFor(() => {
      useGlobalChatStore.getState().sendResourceToChat(PENDING);
    });
    await waitFor(() => {
      expect(screen.getByTestId('staged-resource-chip')).toBeVisible();
    });

    fireEvent.click(screen.getByTitle('Send message'));

    await waitFor(() => {
      expect(screen.getByTestId('staged-resource-chip')).toBeVisible();
    });
  });
});
