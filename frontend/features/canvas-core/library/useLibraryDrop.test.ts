// features/canvas-core/library/useLibraryDrop.test.ts
//
// A drop has to SAY what it did. Every landing used to be `void
// dropLibraryItems(...)`, so a drag whose references all failed to resolve was
// indistinguishable from one that worked — while the panel's CLICK path, on
// the same two services, reported all of it. These cases pin the ladder and
// the one wiring that carries it to a toast.

import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const dropLibraryItems = vi.fn();
vi.mock('./dropLibraryItems', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  dropLibraryItems: (...a: unknown[]) => dropLibraryItems(...a),
}));

const addToast = vi.fn();
vi.mock('../../../components/Toast', () => ({
  useOptionalToast: () => ({ addToast }),
}));

vi.mock('react-i18next', () => ({
  // The real `t` interpolates `{{count}}`; this one does the same substitution
  // so an assertion reads the sentence a user would.
  useTranslation: () => ({
    t: (key: string, o?: { count?: number; defaultValue?: string }) =>
      (o?.defaultValue ?? key).replace('{{count}}', String(o?.count ?? '')),
  }),
}));

import { describeDropOutcome, useLibraryDrop } from './useLibraryDrop';
import type { LibraryDropOutcome, LibraryDropTarget } from './dropLibraryItems';
import type { LibraryItem } from './librarySearch';

const CANVAS: LibraryDropTarget = { kind: 'canvas', position: { x: 0, y: 0 } };
const PROMPT: LibraryDropTarget = { kind: 'prompt', nodeId: 'p1', mention: false };
const MENTION: LibraryDropTarget = { kind: 'prompt', nodeId: 'p1', mention: true };
const MEDIA: LibraryDropTarget = { kind: 'media', nodeId: 'm1' };

const ITEMS: LibraryItem[] = [
  { store: 'generated', id: '800000000000000001', title: 'A wide shot', thumbUrl: '', kind: 'image' },
];

function outcome(over: Partial<LibraryDropOutcome>): LibraryDropOutcome {
  return {
    handled: true, placed: 0, referenced: 0, mentioned: 0,
    failed: 0, skipped: 0, clamped: 0, ...over,
  };
}

// The interpolating stub above, called directly — `describeDropOutcome` takes
// `t` as an argument precisely so the copy is testable without a DOM.
const t = ((key: string, o?: { count?: number; defaultValue?: string }) =>
  (o?.defaultValue ?? key).replace('{{count}}', String(o?.count ?? ''))) as never;

beforeEach(() => {
  addToast.mockReset();
  dropLibraryItems.mockReset().mockResolvedValue(outcome({ placed: 1 }));
});
afterEach(() => vi.clearAllMocks());

describe('describeDropOutcome', () => {
  it('a refusal outranks everything else, and is the only ERROR tone', () => {
    // Both a failure AND a skip: the failure is what the user has to act on.
    const m = describeDropOutcome(outcome({ failed: 2, skipped: 3, referenced: 1 }), PROMPT, t)!;
    expect(m).toEqual({ text: '2 could not be added as references', tone: 'error' });
  });

  it('the PANE landing says "could not be placed" — nothing was ever a reference', () => {
    // An audio upload dropped on the empty pane never attempted a reference,
    // so the reference sentence describes a step that was never taken. The
    // click path has said this all along (`LibraryMediaPage.doPlace`), and the
    // brief's requirement is that click and drop copy agree.
    expect(describeDropOutcome(outcome({ failed: 2 }), CANVAS, t)).toEqual({
      text: '2 could not be placed',
      tone: 'error',
    });
    // The two node landings keep the reference wording, which is true there.
    expect(describeDropOutcome(outcome({ failed: 2 }), MEDIA, t)).toEqual({
      text: '2 could not be added as references',
      tone: 'error',
    });
  });

  it('the ⌥ MENTION landing says so — nothing was attempted as a reference', () => {
    // Same wrongness the pane landing was raised for, on the landing this
    // feature created: dropping a video with ⌥ never tried to add a reference.
    expect(describeDropOutcome(outcome({ failed: 1 }), MENTION, t)).toEqual({
      text: '1 could not be inserted as a mention',
      tone: 'error',
    });
    // And the modifier is what splits them — same node, same failure.
    expect(describeDropOutcome(outcome({ failed: 1 }), PROMPT, t)).toEqual({
      text: '1 could not be added as references',
      tone: 'error',
    });
  });

  it('a drop that changed nothing because it was ALL already there still speaks', () => {
    // Silence here would look exactly like success: nothing moved on screen.
    expect(describeDropOutcome(outcome({ skipped: 2 }), PROMPT, t)).toEqual({
      text: '2 already on this node',
      tone: 'info',
    });
    expect(describeDropOutcome(outcome({ skipped: 2 }), MEDIA, t)).toEqual({
      text: '2 already on this node',
      tone: 'info',
    });
    // The canvas landing says it about the CANVAS — the click path's own split.
    expect(describeDropOutcome(outcome({ skipped: 2 }), CANVAS, t)).toEqual({
      text: '2 already on this canvas',
      tone: 'info',
    });
  });

  it('a partial quota refusal is reported even though some landed', () => {
    expect(describeDropOutcome(outcome({ referenced: 2, clamped: 1 }), PROMPT, t)).toEqual({
      text: '1 references not added · quota reached',
      tone: 'info',
    });
  });

  it('says nothing when the drop simply worked — the board already shows it', () => {
    expect(describeDropOutcome(outcome({ placed: 2 }), CANVAS, t)).toBeNull();
    expect(describeDropOutcome(outcome({ referenced: 2 }), PROMPT, t)).toBeNull();
    // A skip alongside real work is not worth a toast: something DID change.
    expect(describeDropOutcome(outcome({ referenced: 1, skipped: 1 }), PROMPT, t)).toBeNull();
  });
});

describe('useLibraryDrop', () => {
  it('toasts the failure a drop produced, instead of discarding it', async () => {
    dropLibraryItems.mockResolvedValue(outcome({ failed: 2 }));
    const { result } = renderHook(() => useLibraryDrop('s'));

    await result.current(ITEMS, PROMPT);

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('2 could not be added as references', 'error'),
    );
  });

  it('an unexpected throw is reported as every item failing, not as success', async () => {
    // Every known rejection is typed one level down, so this is the guard
    // against a future one — and an unhandled rejection inside a drop handler
    // is indistinguishable from a drop that worked.
    vi.spyOn(console, 'error').mockImplementation(() => {});
    dropLibraryItems.mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useLibraryDrop('s'));

    const got = await result.current(ITEMS, PROMPT);

    expect(got.failed).toBe(ITEMS.length);
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('1 could not be added as references', 'error'),
    );
  });

  it('hands the caller ceiling down and returns the outcome to the caller too', async () => {
    dropLibraryItems.mockResolvedValue(outcome({ referenced: 1 }));
    const { result } = renderHook(() => useLibraryDrop('s', { maxRefs: 3 }));

    const got = await result.current(ITEMS, PROMPT);

    expect(dropLibraryItems).toHaveBeenCalledWith(ITEMS, PROMPT, 's', { maxRefs: 3 });
    expect(got).toMatchObject({ referenced: 1 });
    // A clean drop says nothing — the node already shows the new reference.
    expect(addToast).not.toHaveBeenCalled();
  });
});
