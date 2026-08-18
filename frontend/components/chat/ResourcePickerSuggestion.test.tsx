import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ResourcePickerSuggestion } from './ResourcePickerSuggestion';
import type { ResourceSearchResult } from '../../types';

// Real `/api/v1/resources/search` wire shape (Task 1 contract): every row
// carries `thumbnail_url` (RELATIVE path or null) plus both status columns.
// Hand-idealised fixtures are exactly how the number-vs-string id class of
// bug got to production (CLAUDE.md, 2026-08-12).
const ROWS: ResourceSearchResult[] = [
  { id: '1', name: 'story.md', kind: 'doc', mime: 'text/markdown', size: 100,
    scope: { type: 'personal', id: 'u' }, updated_at: '2026-05-24T00:00:00Z',
    thumbnail_url: null, transcript_status: null, summary_status: null },
  { id: '2', name: 'pitch.mp4', kind: 'video', mime: 'video/mp4', size: 18000000,
    scope: { type: 'team', id: 't1' }, updated_at: '2026-05-20T00:00:00Z',
    thumbnail_url: '/api/v1/resources/2/cover',
    transcript_status: 'none', summary_status: 'none' },
];

const COUNTS = { all: 2, video: 1, image: 0, doc: 1, audio: 0, pdf: 0 };

function renderPicker(items: ResourceSearchResult[] = ROWS, onSelect = vi.fn()) {
  render(
    <ResourcePickerSuggestion
      items={items}
      query=""
      loading={false}
      counts={COUNTS}
      activeKind=""
      onKindChange={() => {}}
      onSelect={onSelect}
    />,
  );
  return onSelect;
}

describe('ResourcePickerSuggestion', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_API_URL', 'https://api.example.test');
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('renders rows with name + scope + relative-time', () => {
    renderPicker();
    expect(screen.getByText('story.md')).toBeInTheDocument();
    expect(screen.getByText('pitch.mp4')).toBeInTheDocument();
    expect(screen.getByText(/personal/i)).toBeInTheDocument();
  });

  it('calls onSelect when row clicked', () => {
    const onSelect = renderPicker();
    fireEvent.click(screen.getByText('story.md').closest('button')!);
    expect(onSelect).toHaveBeenCalledWith(ROWS[0]);
  });

  it('shows empty state when no items', () => {
    render(
      <ResourcePickerSuggestion items={[]} query="xyz" loading={false}
                                counts={{ all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 }}
                                activeKind="" onKindChange={() => {}} onSelect={vi.fn()} />,
    );
    // Accept either the resolved English string OR the raw i18n key (no
    // i18next instance is initialized in unit tests, so useTranslation
    // returns the key verbatim).
    expect(
      screen.getByText(/no resources match|chat\.mentionPicker\.noResults/i),
    ).toBeInTheDocument();
  });

  it('paints the cover thumbnail against the API base when the row has one', () => {
    renderPicker();
    const img = screen.getByTestId('resource-picker-thumb') as HTMLImageElement;
    expect(img.tagName).toBe('IMG');
    expect(img.getAttribute('src')).toBe('https://api.example.test/api/v1/resources/2/cover');
  });

  it('falls back to the kind icon when the row has no cover', () => {
    renderPicker([ROWS[0]]);
    expect(screen.queryByTestId('resource-picker-thumb')).toBeNull();
    expect(screen.getByTestId('resource-picker-icon')).toBeInTheDocument();
  });

  it('falls back to the kind icon when the cover 404s', () => {
    // `/cover` does NOT always answer with a placeholder: for a media_id
    // backed resource whose cover lives in S3 it raises 404
    // (resources_crud_router.py:698-739 has no sb:// branch, so it falls
    // through to :797). Without onError the row paints a broken image and
    // the icon branch — which only covers thumbnail_url === null — never
    // gets a chance to run.
    renderPicker([ROWS[1]]);
    fireEvent.error(screen.getByTestId('resource-picker-thumb'));
    expect(screen.queryByTestId('resource-picker-thumb')).toBeNull();
    expect(screen.getByTestId('resource-picker-icon')).toBeInTheDocument();
  });

  it('badges an untranscribed video so the user knows what they are attaching', () => {
    renderPicker();
    const badges = screen.getAllByTestId('resource-picker-status');
    expect(badges).toHaveLength(1);
    expect(badges[0].getAttribute('data-status')).toBe('unprocessed');
  });

  it('badges an in-flight transcription as processing', () => {
    renderPicker([{ ...ROWS[1], transcript_status: 'processing' }]);
    expect(screen.getByTestId('resource-picker-status').getAttribute('data-status')).toBe('processing');
  });

  it('shows no badge once both steps are done, nor for non audio-visual rows', () => {
    renderPicker([
      ROWS[0],
      { ...ROWS[1], transcript_status: 'completed', summary_status: 'completed' },
    ]);
    expect(screen.queryByTestId('resource-picker-status')).toBeNull();
  });

  it('caps the list height and scrolls instead of clipping rows off-screen', () => {
    // 50 results in a popover with no max-height is the AgentSelector
    // clipping bug again — the tail is unreachable, not just ugly.
    renderPicker();
    const list = screen.getByTestId('resource-picker-list');
    expect(list.className).toMatch(/max-h-/);
    expect(list.className).toMatch(/overflow-y-auto/);
  });

  it('labels the kind tabs without emoji', () => {
    renderPicker();
    const tabs = screen.getByTestId('resource-picker-tabs');
    expect(tabs.textContent ?? '').not.toMatch(/\p{Extended_Pictographic}/u);
  });
});
