import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Music, Trash2 } from 'lucide-react';
import { MobileAudioShell, type MobileAudioShellProps } from './MobileAudioShell';

// MobileAudioShell is the presentational immersive mobile-audio layout. These
// tests pin its view-model contract (title/artist/social/menu/no-audio) so the
// download + upload adapters can map onto it with confidence.

// ── Heavy children / services stubbed so it mounts cleanly in jsdom ──────────
vi.mock('./AudioWaveformPlayer', () => ({
  AudioWaveformPlayer: () => null,
}));
vi.mock('./LyricsOverlay', () => ({
  LyricsOverlay: () => null,
}));
vi.mock('./EagleTagPicker', () => ({
  EagleTagPicker: () => null,
}));
vi.mock('../services/resourceService', () => ({
  fetchResourceTags: vi.fn().mockResolvedValue([]),
  addResourceTag: vi.fn(),
  removeResourceTag: vi.fn(),
}));
vi.mock('../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([]),
  createTag: vi.fn(),
}));
vi.mock('../supabaseClient', () => ({
  getSupabaseClient: vi.fn().mockReturnValue(null),
}));

function renderShell(overrides: Partial<MobileAudioShellProps> = {}) {
  const props: MobileAudioShellProps = {
    src: 'blob:a',
    hasAudio: true,
    title: 'Song',
    lyricLines: [],
    menuItems: [],
    ...overrides,
  };
  return render(<MobileAudioShell {...props} />);
}

describe('MobileAudioShell', () => {
  it('renders the title', () => {
    renderShell();
    expect(screen.getByText('Song')).toBeTruthy();
  });

  it('renders the artist line only when provided', () => {
    const { unmount } = renderShell({ artist: 'The Artist' });
    expect(screen.getByText('The Artist')).toBeTruthy();
    unmount();

    renderShell();
    expect(screen.queryByText('The Artist')).toBeNull();
  });

  it('shows the 1x speed chip and no social counts when social is absent', () => {
    renderShell({ social: null });
    expect(screen.getByText('1x')).toBeTruthy();
    // No social numbers rendered.
    expect(screen.queryByText('1.0K')).toBeNull();
    expect(screen.queryByText('50')).toBeNull();
  });

  it('renders menuItems labels after opening the ⋮ menu', () => {
    renderShell({
      menuItems: [
        { key: 'dl', label: 'Download Audio', Icon: Music, color: 'text-amber-400', onClick: vi.fn() },
        { key: 'del', label: 'Delete', Icon: Trash2, danger: true, onClick: vi.fn(), dividerBefore: true },
      ],
    });

    // Closed initially.
    expect(screen.queryByText('Download Audio')).toBeNull();
    expect(screen.queryByText('Delete')).toBeNull();

    fireEvent.click(screen.getByLabelText('More actions'));

    expect(screen.getByText('Download Audio')).toBeTruthy();
    expect(screen.getByText('Delete')).toBeTruthy();
  });

  it('renders the no-audio prompt when hasAudio is false', () => {
    renderShell({ hasAudio: false, noAudioPrompt: <div>Not downloaded yet</div> });
    expect(screen.getByText('Not downloaded yet')).toBeTruthy();
  });
});
