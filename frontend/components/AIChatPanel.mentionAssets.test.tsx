/**
 * The `@` picker's Assets tab, wired into the real panel.
 *
 * The grid has its own suite (`components/assets/AssetGridPicker.test.tsx`)
 * and the tab seam has one too (`chat/ResourcePickerSuggestion.assets.test`).
 * What only the panel can show is the part where a mistake is invisible: that
 * picking an asset STAGES it rather than inserting a tiptap node, that the
 * literal "@query" is deleted afterwards, and that the arrow keys reach the
 * grid instead of the message.
 *
 * The jsdom geometry stubs in `beforeEach` are load-bearing, not hygiene:
 * prosemirror measures the caret through a Range, and a throw there surfaces
 * as an UNHANDLED error — vitest exits non-zero while still printing
 * "N passed", so reading only the summary line lies.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, def?: unknown) => (typeof def === 'string' ? def : key),
  }),
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

/**
 * `GET /api/v1/assets/search` — mocked at the SERVICE boundary, so the shape
 * here is that function's own documented return: the Envelope is already
 * unwrapped and every BIGINT is already a string (`assets_repository.
 * _serialize` calls `str()`). The wire-level body is exercised in
 * `e2e/chat-assets.spec.ts` instead.
 */
const searchAssetsAccessible = vi.fn();
vi.mock('../services/assetsService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../services/assetsService')>();
  return {
    ...mod,
    searchAssetsAccessible: (...a: unknown[]) => searchAssetsAccessible(...a),
  };
});

/** One resource row, so the default tab is not empty and the two tabs can be
 *  told apart by what they render. */
vi.mock('../hooks/useResourceSearch', () => ({
  useResourceSearch: () => ({
    data: {
      results: [
        {
          id: '339710259795355', name: 'pitch.mp4', kind: 'video', mime: 'video/mp4',
          size: 10, scope: { type: 'personal', id: 'u' },
          updated_at: '2026-09-01T00:00:00Z', thumbnail_url: null,
          transcript_status: 'completed', summary_status: 'completed',
        },
      ],
      counts: { all: 1, video: 1, image: 0, doc: 0, audio: 0, pdf: 0 },
      next_cursor: null,
    },
    loading: false,
    error: null,
  }),
}));
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
// grabs the editor ref the panel hands it, so a test can seed the "@query" a
// user would have typed. jsdom cannot type into contenteditable.
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

const AVA = {
  id: '727145299382534201',
  scope_id: '727145299382534200',
  asset_type: 'character',
  name: 'Ava',
  cover_file_id: '727145299382534301',
  readiness: { state: 'ready', missing: [] },
  is_system_preset: false,
};

const ALLEY = {
  id: '727145299382534202',
  scope_id: '727145299382534200',
  asset_type: 'location',
  name: 'Back Alley',
  cover_file_id: null,
  readiness: { state: 'draft', missing: ['establishing'] },
  is_system_preset: false,
};

async function renderPanel() {
  const view = render(
    <ToastProvider>
      <AIChatPanel projectId="1" agentSlug="analyze" />
    </ToastProvider>,
  );
  await waitFor(() => {
    expect(view.container.querySelector('[contenteditable="true"]')).not.toBeNull();
  });
  await waitFor(() => {
    expect(composerEditor?.current).toBeTruthy();
  });
  return view;
}

/** Type "look at @av" the way a keystroke would: the insert fires tiptap's
 *  update handler, which is what opens the picker. */
async function typeMention(text = ' @av'): Promise<any> {
  const editor = composerEditor!.current;
  editor.commands.setContent('<p>look at</p>');
  editor.commands.focus('end');
  editor.commands.insertContent(text);
  await waitFor(() => {
    expect(screen.getByTestId('resource-picker')).toBeVisible();
  });
  return editor;
}

async function openAssetsTab(): Promise<void> {
  fireEvent.click(screen.getByTestId('resource-picker-tab-assets'));
  await screen.findAllByTestId('mention-asset-option');
}

function attachmentRow(): HTMLElement {
  return screen.getByTestId('composer-attachment-row');
}

function editable(): HTMLElement {
  return document.querySelector('[contenteditable="true"]') as HTMLElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  composerEditor = null;
  Element.prototype.scrollIntoView = vi.fn();
  (Node.prototype as unknown as { getClientRects: () => unknown }).getClientRects =
    () => Object.assign([], { item: () => null });
  (Node.prototype as unknown as { getBoundingClientRect: () => unknown }).getBoundingClientRect =
    () => ({ top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0, x: 0, y: 0 });
  (Range.prototype as unknown as { getClientRects: () => unknown }).getClientRects =
    () => Object.assign([], { item: () => null });
  (Range.prototype as unknown as { getBoundingClientRect: () => unknown }).getBoundingClientRect =
    () => ({ top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0, x: 0, y: 0 });
  useGlobalChatStore.setState({
    pendingAsset: null,
    pendingResource: null,
    pendingQuote: null,
    chatRequest: null,
  });
  searchAssetsAccessible.mockResolvedValue([AVA, ALLEY]);
  streamChatMessage.mockImplementation(async function* () {
    yield { type: 'done', data: {} };
  });
});

describe('opening the Assets tab', () => {
  it('carries the typed query straight into the asset search', async () => {
    await renderPanel();
    await typeMention(' @av');
    await openAssetsTab();
    expect(searchAssetsAccessible).toHaveBeenCalledWith(
      'av',
      expect.objectContaining({ library: 'all', limit: 24 }),
    );
  });

  it('searches by membership — it sends no scope of any kind', async () => {
    // A chat window outlives any one workspace route, so there is no
    // `scope_id` to send. Sending one would be inventing an answer to the
    // authorization question the backend already owns (ruling B/G).
    await renderPanel();
    await typeMention();
    await openAssetsTab();
    const opts = searchAssetsAccessible.mock.calls[0][1] as Record<string, unknown>;
    expect(opts).not.toHaveProperty('scope_id');
    expect(opts).not.toHaveProperty('scopeId');
  });

  it('aborts the in-flight search when the query moves on', async () => {
    await renderPanel();
    await typeMention(' @a');
    await openAssetsTab();
    const first = (searchAssetsAccessible.mock.calls[0][1] as { signal: AbortSignal }).signal;

    composerEditor!.current.commands.insertContent('va');
    await waitFor(() => expect(searchAssetsAccessible.mock.calls.length).toBeGreaterThan(1));
    expect(first.aborted).toBe(true);
  });
});

describe('picking an asset', () => {
  it('stages it above the composer instead of inserting a chip in the sentence', async () => {
    // An asset is a turn-level attachment, not a word: the backend expands it
    // into a consistency prompt plus a primary image. A tiptap node would put
    // it inside the text the user is writing.
    await renderPanel();
    const editor = await typeMention();
    await openAssetsTab();

    fireEvent.mouseDown(screen.getAllByTestId('mention-asset-option')[0]);

    await waitFor(() => {
      expect(within(attachmentRow()).getByTestId('staged-asset-chip')).toBeVisible();
    });
    expect(within(attachmentRow()).getByText('Ava')).toBeVisible();
    expect(screen.queryByTestId('resource-chip')).toBeNull();
    expect(editor.getText()).toBe('look at ');
  });

  it('closes the picker and forgets the tab, so the next @ opens on resources', async () => {
    await renderPanel();
    await typeMention();
    await openAssetsTab();
    fireEvent.mouseDown(screen.getAllByTestId('mention-asset-option')[0]);
    await waitFor(() => expect(screen.queryByTestId('resource-picker')).toBeNull());

    await typeMention(' @av');
    expect(screen.getByTestId('resource-picker-list')).toBeInTheDocument();
    expect(screen.queryByTestId('mention-asset-option')).toBeNull();
  });

  it('snapshots the cover and the scope the search returned, not the asset id', async () => {
    // An asset id is not a resource id; `/resources/{id}/cover` answers 404
    // for one, and the chip would paint a broken image.
    await renderPanel();
    await typeMention();
    await openAssetsTab();
    fireEvent.mouseDown(screen.getAllByTestId('mention-asset-option')[0]);
    await waitFor(() => {
      expect(screen.getByTestId('staged-asset-chip')).toBeVisible();
    });
    const img = screen.getByTestId('staged-asset-chip-thumb') as HTMLImageElement;
    expect(img.getAttribute('src')).toContain('727145299382534301');
    expect(img.getAttribute('src')).not.toContain('727145299382534201');
  });

  it('falls back to the type icon for an asset with no cover', async () => {
    await renderPanel();
    await typeMention();
    await openAssetsTab();
    fireEvent.mouseDown(screen.getAllByTestId('mention-asset-option')[1]);
    await waitFor(() => {
      expect(screen.getByTestId('staged-asset-chip-icon')).toBeVisible();
    });
    expect(screen.queryByTestId('staged-asset-chip-thumb')).toBeNull();
  });

  it('sends it as one asset_ref attachment, in the exact wire shape', async () => {
    await renderPanel();
    await typeMention();
    await openAssetsTab();
    fireEvent.mouseDown(screen.getAllByTestId('mention-asset-option')[0]);
    await waitFor(() => {
      expect(screen.getByTestId('staged-asset-chip')).toBeVisible();
    });

    fireEvent.click(screen.getByTitle('Send message'));
    await waitFor(() => expect(streamChatMessage).toHaveBeenCalled());
    const opts = streamChatMessage.mock.calls[0][2] as {
      attachments: Record<string, unknown>[];
    };
    expect(opts.attachments).toHaveLength(1);
    // Exactly six keys — `asset_type` / `cover_file_id` / `scope_id` are
    // composer-side snapshots the server re-derives, so sending them would
    // state as fact something the receiver ignores.
    expect(Object.keys(opts.attachments[0]).sort()).toEqual([
      'asset_id', 'kind', 'loadout_id', 'mime', 'name', 'url',
    ]);
    expect(opts.attachments[0]).toEqual({
      kind: 'asset_ref',
      asset_id: '727145299382534201',
      loadout_id: null,
      name: 'Ava',
      mime: '',
      url: '',
    });
  });

  it('refuses a second copy of the same asset', async () => {
    // Two `<asset>` entries for one row would be resolved, rendered and
    // billed twice.
    await renderPanel();
    await typeMention();
    await openAssetsTab();
    fireEvent.mouseDown(screen.getAllByTestId('mention-asset-option')[0]);
    await waitFor(() => expect(screen.getByTestId('staged-asset-chip')).toBeVisible());

    await typeMention(' @av');
    await openAssetsTab();
    fireEvent.mouseDown(screen.getAllByTestId('mention-asset-option')[0]);
    await waitFor(() => expect(screen.queryByTestId('resource-picker')).toBeNull());
    expect(screen.getAllByTestId('staged-asset-chip')).toHaveLength(1);
  });
});

describe('the keyboard, while the Assets tab is open', () => {
  it('moves the highlight with the arrows instead of the caret', async () => {
    await renderPanel();
    await typeMention();
    await openAssetsTab();

    const activeIndex = () =>
      screen
        .getAllByTestId('mention-asset-option')
        .findIndex((el) => el.getAttribute('data-active') === 'true');
    expect(activeIndex()).toBe(0);

    fireEvent.keyDown(editable(), { key: 'ArrowDown' });
    await waitFor(() => expect(activeIndex()).toBe(1));
    fireEvent.keyDown(editable(), { key: 'ArrowUp' });
    await waitFor(() => expect(activeIndex()).toBe(0));
  });

  it('Enter picks the highlighted asset rather than sending the message', async () => {
    await renderPanel();
    await typeMention();
    await openAssetsTab();

    fireEvent.keyDown(editable(), { key: 'ArrowDown' });
    await waitFor(() =>
      expect(
        screen.getAllByTestId('mention-asset-option')[1].getAttribute('data-active'),
      ).toBe('true'),
    );
    fireEvent.keyDown(editable(), { key: 'Enter' });

    await waitFor(() => {
      expect(within(attachmentRow()).getByText('Back Alley')).toBeVisible();
    });
    expect(streamChatMessage).not.toHaveBeenCalled();
  });

  it('Enter still sends when the asset list is empty', async () => {
    // `commitActive` declining is what lets the keystroke fall through. A
    // picker that swallowed Enter unconditionally would make the composer
    // stop sending with no explanation on screen.
    searchAssetsAccessible.mockResolvedValue([]);
    await renderPanel();
    await typeMention();
    fireEvent.click(screen.getByTestId('resource-picker-tab-assets'));
    await screen.findByTestId('mention-assets-empty');

    fireEvent.keyDown(editable(), { key: 'Enter' });
    await waitFor(() => expect(streamChatMessage).toHaveBeenCalled());
  });

  it('leaves the arrows alone while the resource tabs are showing', async () => {
    // The resource list has never moved its highlight with the arrows, and
    // claiming the key for a highlight the user cannot see move would be a
    // regression dressed as a feature.
    await renderPanel();
    await typeMention();
    const before = screen.getByTestId('resource-picker').textContent;
    fireEvent.keyDown(editable(), { key: 'ArrowDown' });
    expect(screen.getByTestId('resource-picker').textContent).toBe(before);
  });

  it('Escape closes the picker from the Assets tab too', async () => {
    await renderPanel();
    await typeMention();
    await openAssetsTab();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByTestId('resource-picker')).toBeNull());
  });
});

describe('the resource path is untouched', () => {
  it('still stages a resource row and still clears the @query', async () => {
    await renderPanel();
    const editor = await typeMention(' @pit');
    fireEvent.click(screen.getAllByTestId('resource-picker-row')[0]);
    await waitFor(() => {
      expect(within(attachmentRow()).getByTestId('staged-resource-chip')).toBeVisible();
    });
    expect(editor.getText()).toBe('look at ');
    expect(searchAssetsAccessible).not.toHaveBeenCalled();
  });
});
