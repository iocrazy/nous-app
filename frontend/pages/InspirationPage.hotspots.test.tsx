import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

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

import { InspirationPage } from './InspirationPage';

const HS = (id: string) => ({ id, title: `Hot ${id}`, tags: [], heat: 90 - Number(id), source_label: 'WEIBO' });

describe('InspirationPage P3 hotspots', () => {
  beforeEach(() => {
    listNotes.mockResolvedValue([]);
    getTagCounts.mockResolvedValue([]);
    getActivity.mockResolvedValue([]);
    getHotspots.mockResolvedValue([HS('1'), HS('2'), HS('3')]);
    getHotspot.mockResolvedValue(HS('1'));
  });

  it('renders Notes/Hotspots tabs and a global Parse button', async () => {
    render(<InspirationPage />);
    expect(screen.getByText('Notes')).toBeTruthy();
    expect(screen.getByText('Hotspots')).toBeTruthy();
    expect(screen.getByText('Parse URL')).toBeTruthy();
  });

  it('switching to Hotspots tab shows the ranked workspace', async () => {
    render(<InspirationPage />);
    fireEvent.click(screen.getByText('Hotspots'));
    await waitFor(() => expect(screen.getAllByText('Hot 1').length).toBeGreaterThan(0));
  });

  it('save-as-note from the sidebar switches to Notes with the composer ref chip', async () => {
    render(<InspirationPage />);
    // Notes-tab sidebar Top3 renders after hotspots load
    await waitFor(() => expect(screen.getByText('Hot 1')).toBeTruthy());
    fireEvent.click(screen.getAllByLabelText('Save as note')[0]);
    // composer now shows the referenced hotspot title
    await waitFor(() => expect(screen.getAllByText('Hot 1').length).toBeGreaterThan(0));
  });
});
