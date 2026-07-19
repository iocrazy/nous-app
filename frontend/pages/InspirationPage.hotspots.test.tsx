import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const listNotes = vi.fn();
const getTagCounts = vi.fn();
const getActivity = vi.fn();
const getHotspots = vi.fn();
const getHotspot = vi.fn();
vi.mock('../services/inspirationService', () => ({
  listNotes: (...a: unknown[]) => listNotes(...a),
  getTagCounts: (...a: unknown[]) => getTagCounts(...a),
  getActivity: (...a: unknown[]) => getActivity(...a),
  createNote: vi.fn(), updateNote: vi.fn(), deleteNote: vi.fn(),
  uploadAttachment: vi.fn(), attachmentUrlWithToken: (id: string) => `u/${id}`,
}));
vi.mock('../services/topicService', () => ({
  getHotspots: (...a: unknown[]) => getHotspots(...a),
  getHotspot: (...a: unknown[]) => getHotspot(...a),
  setHotspotState: vi.fn(),
}));
const fetchAllTags = vi.fn();
vi.mock('../services/unifiedTagService', () => ({
  fetchAllTags: (...a: unknown[]) => fetchAllTags(...a),
}));
vi.mock('../components/Inspiration/ActivityPanel', () => ({
  ActivityPanel: ({ onSelectDate }: { onSelectDate: (date: string) => void; selectedDate?: string | null; refreshKey?: number }) => (
    <button data-testid="activity-panel-select-date" onClick={() => onSelectDate('2025-01-15')}>
      Select new date
    </button>
  ),
}));
vi.mock('../components/AILibrary/MarkdownBody', () => ({ default: ({ source }: { source: string }) => <div>{source}</div> }));
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ mediaToken: 'tok' }) }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, f: string) => f }),
  // NoteTimeline/ActivityPanel pull in utils/formatDate.ts -> i18n.ts, which
  // calls `i18n.use(initReactI18next)` at module-eval time — the named
  // export must exist on the mock or vitest throws before any test runs
  // (see pages/InspirationPage.test.tsx for the same fix).
  initReactI18next: { type: '3rdParty', init: () => {} },
}));
vi.mock('../components/Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
// FloatingParse 常驻,mock 掉避免它的 service 依赖
vi.mock('../components/TopicInspiration/FloatingParse', () => ({ FloatingParse: () => <div data-testid="floating-parse" /> }));
// The composer renders TipTap-backed NoteEditor (no textarea); swap in the
// shared textarea shim so the textbox queries below keep working. Same shim
// as the Composer.*.test.tsx files — the specifier resolves to the same
// module ID as Composer's own `./NoteEditor` import, so vitest intercepts it.
vi.mock('../components/Inspiration/NoteEditor', () => import('../components/Inspiration/testing/noteEditorShim'));

import { InspirationPage } from './InspirationPage';

const HS = (id: string) => ({ id, title: `Hot ${id}`, tags: [], heat: 90 - Number(id), source_label: 'WEIBO' });

describe('InspirationPage P3 hotspots', () => {
  beforeEach(() => {
    listNotes.mockResolvedValue([]);
    getTagCounts.mockResolvedValue([]);
    getActivity.mockResolvedValue([]);
    getHotspots.mockResolvedValue([HS('1'), HS('2'), HS('3')]);
    getHotspot.mockResolvedValue(HS('1'));
    fetchAllTags.mockResolvedValue([]);
  });

  it('renders Notes/Hotspots tabs and a global Parse button', async () => {
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    expect(screen.getByText('Notes')).toBeTruthy();
    expect(screen.getByText('Hotspots')).toBeTruthy();
    expect(screen.getByText('Parse URL')).toBeTruthy();
  });

  it('switching to Hotspots tab shows the ranked workspace', async () => {
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    fireEvent.click(screen.getByText('Hotspots'));
    await waitFor(() => expect(screen.getAllByText('Hot 1').length).toBeGreaterThan(0));
  });

  it('save-as-note from the sidebar switches to Notes with the composer ref chip', async () => {
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    // Notes-tab sidebar Top3 renders after hotspots load
    await waitFor(() => expect(screen.getByText('Hot 1')).toBeTruthy());
    fireEvent.click(screen.getAllByLabelText('Save as note')[0]);
    // composer now shows the referenced hotspot title
    await waitFor(() => expect(screen.getAllByText('Hot 1').length).toBeGreaterThan(0));
  });

  it('save-as-note prefills hotspot tags into the composer', async () => {
    getHotspots.mockResolvedValue([{ id: '1', title: 'Hot 1', tags: ['trend'], category: 'food', heat: 90, source_label: 'WEIBO' }]);
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Hot 1')).toBeTruthy());
    fireEvent.click(screen.getAllByLabelText('Save as note')[0]);
    // Two textbox-role elements are on screen (the header search input plus
    // the composer textarea) — disambiguate by tag, not just role.
    const ta = await waitFor(() => {
      const el = screen.getAllByRole('textbox').find((e) => e.tagName === 'TEXTAREA');
      if (!el) throw new Error('composer textarea not found');
      return el as HTMLTextAreaElement;
    });
    expect(ta.value).toContain('#food');
    expect(ta.value).toContain('#trend');
  });

  it('renders pool-tag filter chips on the Hotspots tab (shadow excluded) and filters on click', async () => {
    fetchAllTags.mockResolvedValue([
      { id: 't1', name: 'Trend', name_zh: '趋势', origin: 'curated' },
      { id: 't2', name: 'FromNote', origin: 'note' }, // shadow — must be excluded
    ]);
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);
    fireEvent.click(screen.getByText('Hotspots'));

    // Curated pool tag renders bilingual display name; shadow tag is excluded.
    await waitFor(() => expect(screen.getByText('趋势')).toBeTruthy());
    expect(screen.queryByText('FromNote')).toBeNull();

    // Clicking a tag chip threads its id into getHotspots as the 6th arg.
    getHotspots.mockClear();
    fireEvent.click(screen.getByText('趋势'));
    await waitFor(() =>
      expect(getHotspots).toHaveBeenCalledWith(undefined, undefined, undefined, 'all', undefined, [
        't1',
      ]),
    );
  });

  it('resets category filter when the selected day changes', async () => {
    getHotspots.mockResolvedValue([
      { id: '1', title: 'Hot 1', tags: [], category: 'music', heat: 90, source_label: 'WEIBO' },
      { id: '2', title: 'Hot 2', tags: [], category: 'music', heat: 80, source_label: 'WEIBO' },
      { id: '3', title: 'Hot 3', tags: [], category: 'food', heat: 70, source_label: 'WEIBO' },
    ]);
    render(<MemoryRouter><InspirationPage /></MemoryRouter>);

    // Switch to Hotspots tab
    fireEvent.click(screen.getByText('Hotspots'));

    // Wait for category chips to appear and verify Music chip exists
    await waitFor(() => {
      expect(screen.getByText(/^#music \(/)).toBeTruthy();
    });

    // Click the Music category chip to activate the filter
    const musicChip = screen.getByText(/^#music \(/) as HTMLElement;
    fireEvent.click(musicChip);

    // Verify Music chip is now active (accent-token colored — the accent
    // migration replaced the hardcoded indigo classes with var(--accent-*))
    await waitFor(() => {
      const activeChip = screen.getByText(/^#music \(/).closest('button');
      expect(activeChip?.className).toContain('accent');
    });

    // Trigger date change by clicking the mock ActivityPanel button
    const dateSelectBtn = screen.getByTestId('activity-panel-select-date');
    fireEvent.click(dateSelectBtn);

    // After date change, the Music chip should no longer be active (no accent fill)
    await waitFor(() => {
      const musicChipAfter = screen.getByText(/^#music \(/).closest('button');
      expect(musicChipAfter?.className).not.toContain('accent-soft');
    });
  });
});
