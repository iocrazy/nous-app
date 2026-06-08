import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { LyricsOverlay } from './LyricsOverlay';
import type { SodaTheme } from '../utils/sodaTheme';

// Mock SodaLyricsTab to a marker so we can prove the mediaId/download path is
// taken (or not). The passed-in-lines path must NOT render this.
vi.mock('./SodaLyricsTab', () => ({
  default: () => <div>SODA</div>,
}));

afterEach(cleanup);

// Minimal theme stub — only fields the overlay/LyricsView read.
const theme = {
  gradientCss: 'linear-gradient(#000,#111)',
  accent: '#fff',
  accentSoft: '#222',
  onAccent: '#000',
  lyricActive: '#fff',
  lyricNormal: '#aaa',
} as unknown as SodaTheme;

describe('LyricsOverlay — passed-in lines vs mediaId path', () => {
  it('renders passed-in lines via LyricsView, no SodaLyricsTab, no Fetch button', () => {
    render(
      <LyricsOverlay
        lines={[{ text: 'la', line_start_ms: 0 }]}
        theme={theme}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText('la')).toBeInTheDocument();
    expect(screen.queryByText('SODA')).not.toBeInTheDocument();
    expect(screen.queryByText('Fetch Lyrics')).not.toBeInTheDocument();
  });

  it('renders the SodaLyricsTab (mediaId) path when only mediaId is given', () => {
    render(<LyricsOverlay mediaId="1" theme={theme} onClose={() => {}} />);
    expect(screen.getByText('SODA')).toBeInTheDocument();
  });
});
