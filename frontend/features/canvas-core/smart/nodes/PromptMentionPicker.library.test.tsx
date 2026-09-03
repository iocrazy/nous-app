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
// and `library/LibraryReferencePopover.test.tsx` do.

import { createRef } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k) }),
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
