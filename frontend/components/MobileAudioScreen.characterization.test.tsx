import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MobileAudioScreen } from './MobileAudioScreen';

// MobileAudioScreen is the LOCKED immersive mobile download-audio layout.
// This is a CHARACTERIZATION test: it pins the CURRENT rendered structure so a
// follow-up refactor (extracting a shared shell) can be verified non-regressing.
// It asserts REALITY, not an idealized shape.

// ── Heavy children / services stubbed so it mounts cleanly in jsdom ──────────
// The waveform player touches audio/canvas APIs jsdom lacks.
vi.mock('./AudioWaveformPlayer', () => ({
  AudioWaveformPlayer: () => null,
}));
// Lyrics fetch fires from a mount effect; return an empty, resolved payload.
vi.mock('../services/lyricsService', () => ({
  getMediaLyrics: vi.fn().mockResolvedValue({ lines: [] }),
}));
// Tag picker is only rendered once a resource id resolves; stub it regardless.
vi.mock('./EagleTagPicker', () => ({
  EagleTagPicker: () => null,
}));
// Lyrics overlay is only mounted on demand; stub the module so import is cheap.
vi.mock('./LyricsOverlay', () => ({
  LyricsOverlay: () => null,
}));
// Resource/tag services hit the network; resolve to empty.
vi.mock('../services/resourceService', () => ({
  fetchResourceTags: vi.fn().mockResolvedValue([]),
  addResourceTag: vi.fn(),
  removeResourceTag: vi.fn(),
}));
vi.mock('../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([]),
  createTag: vi.fn(),
}));
// Supabase client is consulted to resolve a resource id when none is passed;
// returning null short-circuits that effect (no resource id → no tag picker).
vi.mock('../supabaseClient', () => ({
  getSupabaseClient: vi.fn().mockReturnValue(null),
}));

const baseVideo = {
  id: '1',
  title: 'Song',
  music_name: null,
  like_count: 1000,
  comment_count: 50,
  share_count: 0,
  favorite_count: 9000,
  source_platform: 'qishui',
  original_url: 'https://x',
};

function renderScreen() {
  return render(
    <MobileAudioScreen
      video={baseVideo as any}
      src="blob:a"
      hasAudio
      mediaId="1"
      onDownloadAudio={vi.fn()}
      onDelete={vi.fn()}
      onCopyLink={vi.fn()}
    />,
  );
}

describe('MobileAudioScreen characterization (pre-refactor net)', () => {
  it('renders the track title', () => {
    renderScreen();
    expect(screen.getByText('Song')).toBeTruthy();
  });

  it('renders non-zero social stats and filters out the zero share stat', () => {
    renderScreen();
    // like_count 1000 → '1.0K', favorite_count 9000 → '9.0K' are visible.
    expect(screen.getByText('1.0K')).toBeTruthy();
    expect(screen.getByText('9.0K')).toBeTruthy();
    // comment_count 50 renders verbatim.
    expect(screen.getByText('50')).toBeTruthy();
    // share_count 0 is filtered by visibleStats — no '0' count rendered.
    expect(screen.queryByText('0')).toBeNull();
  });

  it('renders the playback speed chip at 1x', () => {
    renderScreen();
    expect(screen.getByText('1x')).toBeTruthy();
  });

  it('opens the ⋮ menu and shows Download Audio + Delete actions', () => {
    renderScreen();
    // Menu is closed initially.
    expect(screen.queryByText('Download Audio')).toBeNull();
    expect(screen.queryByText('Delete')).toBeNull();

    fireEvent.click(screen.getByLabelText('More actions'));

    expect(screen.getByText('Download Audio')).toBeTruthy();
    expect(screen.getByText('Delete')).toBeTruthy();
  });
});
