/**
 * The P5 chain end to end in the real panel: an asset sent from the library
 * lands in the attachment row above the composer, goes out in the exact
 * `asset_ref` shape `AttachmentRequest` reads, and its typed failure comes
 * back as a banner the user can act on.
 *
 * The unit suites cover the pieces (stagedAssets.test.ts for the wire shape,
 * useComposerAssetAttach.test.tsx for the channel, the banner's own file for
 * the copy). This one covers the wiring between them — where a prop handed to
 * the wrong component, or a list left out of the send payload, would hide.
 *
 * Structured after `AIChatPanel.chipAttachment.test.tsx`, including its jsdom
 * geometry stubs: without them prosemirror's caret measurement throws as an
 * UNHANDLED error and vitest exits non-zero while still printing "N passed".
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

/**
 * What the server hands back on the post-send history reload. It matters that
 * this is the PERSISTED attachment shape and not the composer's: the reducer
 * whitelist keeps `kind / asset_id / loadout_id / mime / alt_text / name` and
 * drops everything else, so a bubble that needed `asset_type` would pass a
 * test seeded from the outgoing payload and render blank in production.
 *
 * Empty until a turn is sent — the panel renders its empty state (and, with
 * it, no banner) while a session has no messages.
 */
let history: unknown[] = [];
const PERSISTED_TURN = {
  id: 'm-1',
  session_id: 'sess-1',
  role: 'user',
  content: '',
  attachments: [
    {
      kind: 'asset_ref',
      asset_id: '727145299382534300',
      loadout_id: null,
      name: 'Sang Yao',
      mime: '',
    },
  ],
  created_at: '2026-09-03T00:00:00Z',
};

vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: {
    listAgents: vi.fn(async () => [
      { id: '1', slug: 'analyze', name: 'Analyze', enabled: true },
    ]),
    listChatSessions: vi.fn(async () => [SESSION]),
    listAllChatSessions: vi.fn(async () => []),
    getChatSession: vi.fn(async () => ({ ...SESSION, messages: history })),
    createChatSession: vi.fn(async () => SESSION),
    updateChatSession: vi.fn(async () => SESSION),
    deleteChatSession: vi.fn(async () => undefined),
    streamChatMessage: (...args: unknown[]) => streamChatMessage(...args),
    uploadChatAttachment: vi.fn(),
  },
}));

vi.mock('../hooks/useResourceSearch', () => ({
  useResourceSearch: () => ({ data: { results: [], counts: {} }, loading: false, error: null }),
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

import { AIChatPanel } from './AIChatPanel';
import { ToastProvider } from './Toast';
import { useGlobalChatStore } from '../stores/globalChatStore';

/** What `sendAssetToAgent` hands the store. Ids are STRINGS, as the assets
 *  router emits them; `loadoutId` is null in v1. */
const PENDING_ASSET = {
  assetId: '727145299382534300',
  loadoutId: null,
  name: 'Sang Yao',
  assetType: 'character',
  coverFileId: '727145299382534146',
  scopeId: '727145299382534200',
};

const PENDING_RESOURCE = {
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
  await waitFor(() => {
    expect(view.container.querySelector('[contenteditable="true"]')).not.toBeNull();
  });
  return view;
}

function attachmentRow(): HTMLElement {
  return screen.getByTestId('composer-attachment-row');
}

async function stagePendingAsset() {
  await waitFor(() => {
    useGlobalChatStore.getState().sendAssetToChat(PENDING_ASSET);
  });
  await waitFor(() => {
    expect(within(attachmentRow()).getByTestId('staged-asset-chip')).toBeVisible();
  });
}

beforeEach(() => {
  vi.clearAllMocks();
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
  history = [];
  streamChatMessage.mockImplementation(async function* () {
    history = [PERSISTED_TURN];
    yield { type: 'done', data: {} };
  });
});

describe('an asset sent from the library lands above the composer', () => {
  it('stages the asset chip with its name', async () => {
    await renderPanel();
    await stagePendingAsset();
    expect(within(attachmentRow()).getByText('Sang Yao')).toBeVisible();
  });

  it('paints the cover from cover_file_id, not from the asset id', async () => {
    // An asset id is not a resource id; asking the cover route about it 404s.
    await renderPanel();
    await stagePendingAsset();
    const img = screen.getByTestId('staged-asset-chip-thumb') as HTMLImageElement;
    expect(img.getAttribute('src')).toContain('727145299382534146');
    expect(img.getAttribute('src')).not.toContain('727145299382534300');
  });

  it('falls back to the type icon when the asset has no cover', async () => {
    await renderPanel();
    await waitFor(() => {
      useGlobalChatStore
        .getState()
        .sendAssetToChat({ ...PENDING_ASSET, coverFileId: null });
    });
    await waitFor(() => {
      expect(screen.getByTestId('staged-asset-chip-icon')).toBeVisible();
    });
    expect(screen.queryByTestId('staged-asset-chip-thumb')).toBeNull();
  });

  it('drops the chip again when the × is clicked', async () => {
    await renderPanel();
    await stagePendingAsset();
    fireEvent.click(within(screen.getByTestId('staged-asset-chip')).getByLabelText('remove'));
    await waitFor(() => {
      expect(screen.queryByTestId('staged-asset-chip')).toBeNull();
    });
  });
});

describe('sending', () => {
  it('sends the exact asset_ref wire shape', async () => {
    await renderPanel();
    await stagePendingAsset();

    fireEvent.click(screen.getByTitle('Send message'));

    await waitFor(() => expect(streamChatMessage).toHaveBeenCalled());
    const [, , opts] = streamChatMessage.mock.calls[0] as [string, string, any];
    expect(opts.attachments).toEqual([
      {
        kind: 'asset_ref',
        asset_id: '727145299382534300',
        loadout_id: null,
        name: 'Sang Yao',
        mime: '',
        url: '',
      },
    ]);
  });

  it('sends an asset and a resource in one turn, each in its own shape', async () => {
    await renderPanel();
    await stagePendingAsset();
    await waitFor(() => {
      useGlobalChatStore.getState().sendResourceToChat(PENDING_RESOURCE);
    });
    await waitFor(() => {
      expect(screen.getByTestId('staged-resource-chip')).toBeVisible();
    });

    fireEvent.click(screen.getByTitle('Send message'));

    await waitFor(() => expect(streamChatMessage).toHaveBeenCalled());
    const [, , opts] = streamChatMessage.mock.calls[0] as [string, string, any];
    expect(opts.attachments).toEqual([
      {
        kind: 'resource_ref',
        url: '',
        resource_id: '339710259795355',
        mime: 'video/mp4',
        alt_text: 'pitch.mp4',
      },
      {
        kind: 'asset_ref',
        asset_id: '727145299382534300',
        loadout_id: null,
        name: 'Sang Yao',
        mime: '',
        url: '',
      },
    ]);
  });

  it('sends an asset-only turn, with no text typed', async () => {
    await renderPanel();
    await stagePendingAsset();
    fireEvent.click(screen.getByTitle('Send message'));
    await waitFor(() => expect(streamChatMessage).toHaveBeenCalled());
    const [, text] = streamChatMessage.mock.calls[0] as [string, string, unknown];
    expect(text).toBe('');
  });

  it('sends one asset once, however many times it was staged', async () => {
    await renderPanel();
    await stagePendingAsset();
    // A second Send To Agent on the SAME asset: the channel re-fires (new
    // nonce) but the row must not gain a duplicate.
    await waitFor(() => {
      useGlobalChatStore.getState().sendAssetToChat(PENDING_ASSET);
    });
    await waitFor(() => {
      expect(useGlobalChatStore.getState().pendingAsset).toBeNull();
    });
    expect(screen.getAllByTestId('staged-asset-chip')).toHaveLength(1);

    fireEvent.click(screen.getByTitle('Send message'));
    await waitFor(() => expect(streamChatMessage).toHaveBeenCalled());
    const [, , opts] = streamChatMessage.mock.calls[0] as [string, string, any];
    expect(opts.attachments).toHaveLength(1);
  });

  it('clears the attachment row once the turn is sent', async () => {
    await renderPanel();
    await stagePendingAsset();
    fireEvent.click(screen.getByTitle('Send message'));
    await waitFor(() => {
      expect(screen.queryByTestId('staged-asset-chip')).toBeNull();
    });
  });

  it('surfaces a typed asset failure as a banner with its reason', async () => {
    // The real `done` payload: index is a position in the FULL attachment
    // list, kind is `asset_ref`, reason is one of ruling C's four codes.
    streamChatMessage.mockImplementation(async function* () {
      history = [PERSISTED_TURN];
      yield {
        type: 'done',
        data: {
          attachment_failures: [
            { index: 0, kind: 'asset_ref', reason: 'asset_no_primary_image' },
          ],
        },
      };
    });
    await renderPanel();
    await stagePendingAsset();

    fireEvent.click(screen.getByTitle('Send message'));

    await waitFor(() => {
      expect(screen.getByTestId('attachment-failure-reason')).toBeVisible();
    });
    expect(
      screen.getByTestId('attachment-failure-reason').getAttribute('data-reason'),
    ).toBe('asset_no_primary_image');
  });
});

describe('history', () => {
  it('renders the asset chip from the authoritative row after the reload', async () => {
    // The reloaded bubble knows the asset only by the whitelisted keys. If the
    // chip needed anything else it would go blank exactly here — one refetch
    // after it looked fine.
    await renderPanel();
    await stagePendingAsset();

    fireEvent.click(screen.getByTitle('Send message'));

    await waitFor(() => {
      expect(screen.getByTestId('bubble-asset-chip')).toBeVisible();
    });
    expect(screen.getByTestId('bubble-asset-chip').textContent).toContain('Sang Yao');
  });
});
