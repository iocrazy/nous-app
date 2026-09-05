// features/canvas-core/smart/nodes/PromptMentionPicker.library.test.tsx
//
// The `@` palette's two NEW groups. #2097 gave it Input images + Assets; the
// library panel work adds Uploads and Generated · this canvas, so the whole
// library is reachable from the caret without leaving the sentence.
//
// The consequence line is pinned by text, not by snapshot: it is the one thing
// on screen that distinguishes "this becomes a reference" from "this becomes
// a chip", and a snapshot refresh would let it silently change.
//
// jsdom lays nothing out, so the justified pre-pass would see a container
// width of 0 and return NO rows — the grid would render zero cells whatever
// the data said. The ResizeObserver / getBoundingClientRect stubs below are
// what let the REAL layout path run, exactly as `library/LibraryGrid.test.tsx`
// and `library/LibraryPanel.test.tsx` do.

import { createRef } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) =>
      typeof d === 'string'
        ? d
        : typeof d === 'object' && d !== null && 'defaultValue' in (d as object)
          ? String((d as { defaultValue: string }).defaultValue).replace(
              /\{\{count\}\}/g,
              String((d as { count?: number }).count ?? ''),
            )
          : k,
  }),
}));

const searchAssets = vi.fn();
const searchResources = vi.fn();
const fetchGenerated = vi.fn();

vi.mock('../../../../services/assetsService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  searchAssets: (...a: unknown[]) => searchAssets(...a),
}));
vi.mock('../../../../services/resourceSearchService', () => ({
  searchResources: (...a: unknown[]) => searchResources(...a),
}));
vi.mock('../../../../services/generatedService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchGenerated: (...a: unknown[]) => fetchGenerated(...a),
}));

import {
  PromptMentionPicker,
  type PromptMentionPickerHandle,
} from './PromptMentionPicker';

const SCOPE = '727145299382534100';
const CANVAS = '900000000000000001';

const UPLOAD_ROW = {
  id: '655000000000000001',
  name: 'harbour-dusk.png',
  kind: 'image' as const,
  mime: 'image/png',
  size: 1,
  scope: { type: 'team' as const, id: SCOPE },
  updated_at: '2026-09-01T10:11:12Z',
  thumbnail_url: '/api/v1/resources/655000000000000001/cover',
  transcript_status: null,
  summary_status: null,
};
const UPLOAD_ROW_2 = {
  ...{
    id: '655000000000000002',
    name: 'harbour-dawn.png',
    kind: 'image' as const,
    mime: 'image/png',
    size: 1,
    scope: { type: 'team' as const, id: SCOPE },
    updated_at: '2026-09-01T10:11:13Z',
    thumbnail_url: '/api/v1/resources/655000000000000002/cover',
    transcript_status: null,
    summary_status: null,
  },
};
const GENERATED_ROW = {
  id: '800000000000000001',
  scope_id: SCOPE,
  media_kind: 'image',
  mime: 'image/png',
  prompt: 'harbour',
  model: 'doubao-seedream',
  provider: 'volcengine',
  origin_kind: 'canvas_run',
  canvas_id: CANVAS,
  node_id: 'node-7',
  created_at: '2026-09-02T12:00:00Z',
  promoted_resource_id: null,
  review_state: 'unreviewed' as const,
  source_asset_id: null,
  source: {
    kind: 'canvas_run',
    label: 'Canvas',
    canvas_id: CANVAS,
    node_id: 'node-7',
    shot_id: null,
    conversation_id: null,
    deep_link: null,
  },
  title: 'A wide shot of the harbour',
};

const onPickLibraryImage = vi.fn();

class ObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): [] {
    return [];
  }
}

function renderPicker(inputImages: Array<{ url: string; label: string }> = []) {
  return render(
    <PromptMentionPicker
      scopeId={SCOPE}
      canvasId={CANVAS}
      inputImages={inputImages}
      onPickImage={vi.fn()}
      onPickAsset={vi.fn()}
      onPickLibraryImage={onPickLibraryImage}
      query=""
    />,
  );
}

let realRect: () => DOMRect;
beforeEach(() => {
  // Classes, not `vi.fn().mockImplementation(() => ({…}))`: the virtualizer
  // calls `new ResizeObserver(…)`, and vitest 4 cannot construct a mock whose
  // implementation is an arrow function.
  global.ResizeObserver = ObserverStub as unknown as typeof ResizeObserver;
  global.IntersectionObserver = ObserverStub as unknown as typeof IntersectionObserver;
  realRect = HTMLElement.prototype.getBoundingClientRect;
  HTMLElement.prototype.getBoundingClientRect = function () {
    return { width: 640, height: 480, top: 0, left: 0, right: 640, bottom: 480, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  };
  onPickLibraryImage.mockReset();
  searchAssets.mockReset().mockResolvedValue([]);
  searchResources.mockReset().mockResolvedValue({
    results: [UPLOAD_ROW],
    counts: { all: 1, video: 0, image: 1, doc: 0, audio: 0, pdf: 0 },
    next_cursor: null,
  });
  fetchGenerated.mockReset().mockResolvedValue({ items: [GENERATED_ROW], next_cursor: null });
});
afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = realRect;
  cleanup();
});

describe('PromptMentionPicker library groups', () => {
  it('states the three different consequences before anything is picked', () => {
    renderPicker();
    const line = screen.getByTestId('mention-consequence').textContent ?? '';
    expect(line).toContain('sends its reference files at run');
    expect(line).toContain('adds it as a reference');
    expect(line).toContain('Plain text stays text');
  });

  it('offers four groups, in a fixed order', () => {
    renderPicker();
    expect(screen.getAllByTestId(/^mention-tab-/).map((b) => b.getAttribute('data-testid'))).toEqual([
      'mention-tab-input',
      'mention-tab-assets',
      'mention-tab-uploads',
      'mention-tab-generated',
    ]);
  });

  it('the Uploads group lists library images', async () => {
    renderPicker();
    fireEvent.click(screen.getByTestId('mention-tab-uploads'));
    await waitFor(() => expect(screen.getAllByTestId('library-cell')).toHaveLength(1));
    expect(searchResources).toHaveBeenCalledWith(
      expect.objectContaining({ teamId: SCOPE }),
    );
  });

  it('the Files group carries the same four source chips as the panel', async () => {
    // One shelf under two entry points. The palette showing every source
    // while the panel could narrow would be the same shelf answering two
    // different questions depending on how it was opened.
    renderPicker();
    fireEvent.click(screen.getByTestId('mention-tab-uploads'));
    const chips = screen.getAllByTestId('mention-source-chip');
    expect(chips.map((c) => c.getAttribute('data-source'))).toEqual([
      'all',
      'upload',
      'web',
      'generated',
    ]);
    expect(chips[0].getAttribute('aria-pressed')).toBe('true');
  });

  it('picking Downloaded re-queries the Files group with sources=web', async () => {
    renderPicker();
    fireEvent.click(screen.getByTestId('mention-tab-uploads'));
    await waitFor(() => expect(searchResources).toHaveBeenCalled());
    searchResources.mockClear();
    fireEvent.click(
      screen.getAllByTestId('mention-source-chip').find(
        (c) => c.getAttribute('data-source') === 'web',
      ) as HTMLElement,
    );
    await waitFor(() => expect(searchResources).toHaveBeenCalled());
    expect(searchResources.mock.calls[0][0].sources).toBe('web');
  });

  it('the source chips belong to Files alone', () => {
    renderPicker();
    fireEvent.click(screen.getByTestId('mention-tab-generated'));
    expect(screen.queryAllByTestId('mention-source-chip')).toHaveLength(0);
  });

  it('the Generated group asks for THIS canvas, not the whole scope', async () => {
    renderPicker();
    fireEvent.click(screen.getByTestId('mention-tab-generated'));
    await waitFor(() => expect(fetchGenerated).toHaveBeenCalled());
    expect(fetchGenerated).toHaveBeenCalledWith(
      SCOPE,
      expect.objectContaining({ canvasId: CANVAS }),
    );
  });

  it('picking from a library group reports the ITEM, so the caller can add a reference', async () => {
    renderPicker();
    fireEvent.click(screen.getByTestId('mention-tab-uploads'));
    await waitFor(() => expect(screen.getAllByTestId('library-cell')).toHaveLength(1));
    fireEvent.doubleClick(screen.getAllByTestId('library-cell')[0]);
    expect(onPickLibraryImage).toHaveBeenCalledWith(
      expect.objectContaining({ store: 'uploads', id: '655000000000000001' }),
    );
  });

  it('ONE click picks — the gesture the Input Images and Assets tabs already use', async () => {
    // The grid drives its ring from a controlled `activeIndex` and its
    // selection from `selection={[]}`, so a no-op `onSelectionChange` left a
    // single click reaching NOTHING: no ring move, no pick, no error. The two
    // other tabs of this same popover commit on one press, so one palette
    // taught two gestures depending on which tab you were on.
    renderPicker();
    fireEvent.click(screen.getByTestId('mention-tab-uploads'));
    await waitFor(() => expect(screen.getAllByTestId('library-cell')).toHaveLength(1));

    fireEvent.click(screen.getAllByTestId('library-cell')[0]);

    expect(onPickLibraryImage).toHaveBeenCalledTimes(1);
    expect(onPickLibraryImage).toHaveBeenCalledWith(
      expect.objectContaining({ store: 'uploads', id: '655000000000000001' }),
    );
  });

  it('a real DOUBLE click commits once, not three times', async () => {
    // A browser double-click dispatches `click`, `click`, `dblclick`. All three
    // reach a commit now that a single click picks — the first two through the
    // grid's selection change, the third through `onItemActivate` — and
    // nothing closes the popover in between: the close happens in the CALLER,
    // after the round trip. For a generated image the extras dedupe by url;
    // for an UPLOAD every resolve mints a fresh `generated_media` row with a
    // fresh url, so dedupe cannot fire and one gesture spent three reference
    // slots and left two orphan inbox rows.
    //
    // The pick is left PENDING on purpose: that is the window the burst lands
    // in, and a promise that had already settled would not test the guard.
    onPickLibraryImage.mockImplementation(() => new Promise(() => {}));
    renderPicker();
    fireEvent.click(screen.getByTestId('mention-tab-uploads'));
    await waitFor(() => expect(screen.getAllByTestId('library-cell')).toHaveLength(1));

    const cell = screen.getAllByTestId('library-cell')[0];
    fireEvent.click(cell);
    fireEvent.click(cell);
    fireEvent.doubleClick(cell);

    expect(onPickLibraryImage).toHaveBeenCalledTimes(1);
  });

  it('Tab walks the groups and wraps', () => {
    renderPicker([{ url: '/api/v1/generated-media/1/file', label: 'Image 1' }]);
    const root = screen.getByTestId('prompt-mention-picker');
    expect(screen.getByTestId('mention-tab-input')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.keyDown(root, { key: 'Tab' });
    expect(screen.getByTestId('mention-tab-assets')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.keyDown(root, { key: 'Tab' });
    fireEvent.keyDown(root, { key: 'Tab' });
    expect(screen.getByTestId('mention-tab-generated')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.keyDown(root, { key: 'Tab' });
    expect(screen.getByTestId('mention-tab-input')).toHaveAttribute('aria-pressed', 'true');
  });

  // A real user's focus is in the BODY EDITOR, never in this popover — every
  // pick is bound to `mousedown` with `preventDefault` precisely so the editor
  // keeps it. So the root's own `onKeyDown` above fires for a synthetic
  // dispatch and for nobody else; `⇥` reaches a person only through the
  // imperative handle the editor forwards to.
  it('the editor can cycle groups through the imperative handle', () => {
    const ref = createRef<PromptMentionPickerHandle>();
    render(
      <PromptMentionPicker
        ref={ref}
        scopeId={SCOPE}
        canvasId={CANVAS}
        inputImages={[{ url: '/api/v1/generated-media/1/file', label: 'Image 1' }]}
        onPickImage={vi.fn()}
        onPickAsset={vi.fn()}
        onPickLibraryImage={onPickLibraryImage}
        query=""
      />,
    );
    expect(screen.getByTestId('mention-tab-input')).toHaveAttribute('aria-pressed', 'true');
    act(() => ref.current?.cycleTab());
    expect(screen.getByTestId('mention-tab-assets')).toHaveAttribute('aria-pressed', 'true');
  });

  // The ring the user sees and the row Enter commits have to be the SAME cell.
  // The grid owns its own cursor and the editor's arrows drive the picker's,
  // so without a controlled `activeIndex` those two walk apart silently.
  it('the arrow keys move the ring the editor will commit from', async () => {
    searchResources.mockResolvedValue({
      results: [UPLOAD_ROW, UPLOAD_ROW_2],
      counts: { all: 2, video: 0, image: 2, doc: 0, audio: 0, pdf: 0 },
      next_cursor: null,
    });
    const ref = createRef<PromptMentionPickerHandle>();
    render(
      <PromptMentionPicker
        ref={ref}
        scopeId={SCOPE}
        canvasId={CANVAS}
        inputImages={[]}
        onPickImage={vi.fn()}
        onPickAsset={vi.fn()}
        onPickLibraryImage={onPickLibraryImage}
        query=""
      />,
    );
    fireEvent.click(screen.getByTestId('mention-tab-uploads'));
    await waitFor(() => expect(screen.getAllByTestId('library-cell')).toHaveLength(2));

    act(() => ref.current?.move(1));
    expect(screen.getAllByTestId('library-cell')[1]).toHaveAttribute('data-active', 'true');
    expect(screen.getAllByTestId('library-cell')[0]).not.toHaveAttribute('data-active');
    act(() => {
      ref.current?.commitActive();
    });
    expect(onPickLibraryImage).toHaveBeenCalledWith(
      expect.objectContaining({ store: 'uploads', id: '655000000000000002' }),
    );
  });

  // A pick that changes nothing must SAY so. `addReferences` answers a typed
  // result and the caller hands it back; swallowing it is the silent no-op the
  // repo keeps re-learning.
  it('a pick that adds nothing says why instead of just closing', async () => {
    onPickLibraryImage.mockResolvedValue({ added: 0, skipped: 1, failed: [], clamped: 0 });
    renderPicker();
    fireEvent.click(screen.getByTestId('mention-tab-uploads'));
    await waitFor(() => expect(screen.getAllByTestId('library-cell')).toHaveLength(1));
    fireEvent.doubleClick(screen.getAllByTestId('library-cell')[0]);
    await waitFor(() =>
      expect(screen.getByTestId('mention-notice').textContent).toBe('1 already on this node'),
    );
  });

  it('with no canvas id the Generated group says so instead of listing the scope', async () => {
    render(
      <PromptMentionPicker
        scopeId={SCOPE}
        canvasId={null}
        inputImages={[]}
        onPickImage={vi.fn()}
        onPickAsset={vi.fn()}
        onPickLibraryImage={onPickLibraryImage}
        query=""
      />,
    );
    fireEvent.click(screen.getByTestId('mention-tab-generated'));
    await waitFor(() => expect(screen.getByTestId('library-empty')).toBeInTheDocument());
    expect(fetchGenerated).not.toHaveBeenCalled();
  });
});
