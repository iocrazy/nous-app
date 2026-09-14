/**
 * useLibrary's realtime subscriptions — specifically, the race that made a
 * freshly-downloaded item invisible until a full page refresh.
 *
 * A download writes TWO rows: `parsed_media` (the media itself) and
 * `resources` (this user's ownership of it). Their order is not guaranteed.
 * The list only ever learned about new items from the `parsed_media` INSERT
 * event, and that handler confirms ownership by querying `resources`:
 *
 *     const { data: resource } = await supabase.from('resources')…
 *     if (!resource) return;              // ← dropped, forever
 *
 * That empty answer has two meanings — "not yours" and "not written yet" —
 * and the handler collapsed them into the first. The `resources` channel could
 * not rescue it either: it subscribed to UPDATE only, and its callback bailed
 * on any media not already in the list. So when `resources` landed second, the
 * item existed in the database and nowhere on screen until the user reloaded.
 *
 * The same gap produced the second symptom the user reported: a row that does
 * reach the list without a `resource_id` cannot navigate — `handleNavigateToDetail`
 * needs that id and otherwise says "Still processing".
 *
 * The fix is event-driven, not a retry loop: subscribe to `resources` INSERT
 * as well, so whichever of the two rows lands LAST is the one that adds the
 * item. These tests fire the two orders separately, which is the only way to
 * tell a fixed race from a lucky one.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, act, waitFor } from '@testing-library/react';
import React from 'react';

type Handler = (payload: Record<string, unknown>) => void | Promise<void>;

/** Captured `.on('postgres_changes', cfg, handler)` registrations, by table+event. */
const handlers = new Map<string, Handler>();
/** Rows the fake `resources` table will answer with, keyed by media_id. */
let resourceRows: Record<string, { id: string } | null> = {};
/** Rows the fake `parsed_media` table will answer with, keyed by id. */
let mediaRows: Record<string, Record<string, unknown> | null> = {};

function fakeQuery(table: string) {
  const filters: Record<string, string> = {};
  const builder: Record<string, unknown> = {
    select: () => builder,
    limit: () => builder,
    eq: (col: string, val: string) => {
      filters[col] = String(val);
      return builder;
    },
    maybeSingle: async () => {
      if (table === 'resources') return { data: resourceRows[filters.media_id] ?? null };
      if (table === 'parsed_media') return { data: mediaRows[filters.id] ?? null };
      return { data: null };
    },
  };
  return builder;
}

vi.mock('../supabaseClient', () => ({
  isSupabaseConfigured: () => true,
  getSupabaseClient: () => ({
    auth: { getSession: async () => ({ data: { session: { user: { id: 'me' } } } }) },
    from: (table: string) => fakeQuery(table),
    channel: () => {
      const ch: Record<string, unknown> = {
        on: (_evt: string, cfg: { table: string; event: string }, handler: Handler) => {
          handlers.set(`${cfg.table}:${cfg.event}`, handler);
          return ch;
        },
        subscribe: () => ch,
      };
      return ch;
    },
    removeChannel: () => {},
  }),
}));

vi.mock('../services/dataService', () => ({
  // The REAL shape `loadLibraryData` destructures — `{ data, hasMore,
  // nextCursor, totalCount }`, not a tidier `{ items, total }`. Getting this
  // wrong made `setLibrary(undefined)` and every assertion below fail for a
  // reason that had nothing to do with the subject (CLAUDE.md: 边界 mock 必须
  // 用真实 JSON 形状).
  fetchLibraryPaginated: vi
    .fn()
    .mockResolvedValue({ data: [], hasMore: false, nextCursor: null, totalCount: 0 }),
  updateItem: vi.fn(),
  deleteItem: vi.fn(),
}));
vi.mock('../services/collectionService', () => ({
  fetchMyCollections: vi.fn().mockResolvedValue([]),
  createCollection: vi.fn(),
  fetchVideoCollections: vi.fn().mockResolvedValue([]),
  addVideoToCollection: vi.fn(),
  removeVideoFromCollection: vi.fn(),
}));
vi.mock('../constants', () => ({ MOCK_LIBRARY: [] }));

import { useLibrary } from './useLibrary';

let libraryRef: Array<Record<string, unknown>> = [];

function Harness() {
  const { library } = useLibrary({ isAuthenticated: true, selectedTeamId: null });
  libraryRef = library as unknown as Array<Record<string, unknown>>;
  return <span data-testid="n">{library.length}</span>;
}

/** Mount and wait for the subscriptions to be registered (they are gated on
 *  the initial load finishing, so this also proves that gate still holds). */
async function mount() {
  handlers.clear();
  render(<Harness />);
  await waitFor(() => expect(handlers.has('resources:INSERT')).toBe(true));
}

const MEDIA = { id: '900', platform_id: 'p900', title: 'Fresh download' };

beforeEach(() => {
  resourceRows = {};
  mediaRows = {};
  libraryRef = [];
  vi.clearAllMocks();
});

describe('a new download reaches the list whichever row lands first', () => {
  it('resources first, then parsed_media (the order that already worked)', async () => {
    await mount();
    resourceRows['900'] = { id: 'r900' };

    await act(async () => {
      await handlers.get('parsed_media:*')?.({ eventType: 'INSERT', new: MEDIA });
    });

    expect(libraryRef).toHaveLength(1);
    expect(libraryRef[0].resource_id).toBe('r900');
  });

  // The reported bug. The ownership query answers empty because the row is not
  // written yet — not because the media belongs to someone else.
  it('parsed_media first, then resources (the order that lost the item)', async () => {
    await mount();

    // 1. parsed_media arrives while `resources` is still being written.
    await act(async () => {
      await handlers.get('parsed_media:*')?.({ eventType: 'INSERT', new: MEDIA });
    });
    expect(libraryRef).toHaveLength(0);

    // 2. `resources` lands. THIS is what used to be missing entirely.
    mediaRows['900'] = MEDIA;
    await act(async () => {
      await handlers.get('resources:INSERT')?.({
        eventType: 'INSERT',
        new: { id: 'r900', media_id: '900', creator_id: 'me' },
      });
    });

    expect(libraryRef).toHaveLength(1);
    expect(libraryRef[0].id).toBe('900');
  });

  // Without the id the card is in the list but cannot be opened — the second
  // symptom in the same report ("双击打不开，要刷新").
  it('the row added from the resources side carries its resource_id', async () => {
    await mount();
    mediaRows['900'] = MEDIA;

    await act(async () => {
      await handlers.get('resources:INSERT')?.({
        eventType: 'INSERT',
        new: { id: 'r900', media_id: '900', creator_id: 'me' },
      });
    });

    expect(libraryRef[0].resource_id).toBe('r900');
  });
});

describe('the resources INSERT handler does not manufacture rows', () => {
  it('adds nothing when the media row cannot be read', async () => {
    await mount();
    // No entry in `mediaRows` — the media is gone, or not replicated yet.
    await act(async () => {
      await handlers.get('resources:INSERT')?.({
        eventType: 'INSERT',
        new: { id: 'r901', media_id: '901', creator_id: 'me' },
      });
    });
    expect(libraryRef).toHaveLength(0);
  });

  it('does not duplicate an item the parsed_media side already added', async () => {
    await mount();
    resourceRows['900'] = { id: 'r900' };
    mediaRows['900'] = MEDIA;

    await act(async () => {
      await handlers.get('parsed_media:*')?.({ eventType: 'INSERT', new: MEDIA });
    });
    await act(async () => {
      await handlers.get('resources:INSERT')?.({
        eventType: 'INSERT',
        new: { id: 'r900', media_id: '900', creator_id: 'me' },
      });
    });

    expect(libraryRef).toHaveLength(1);
  });

  it('ignores an INSERT with no media_id rather than querying for null', async () => {
    await mount();
    await act(async () => {
      await handlers.get('resources:INSERT')?.({
        eventType: 'INSERT',
        new: { id: 'r902', media_id: null, creator_id: 'me' },
      });
    });
    expect(libraryRef).toHaveLength(0);
  });
});
