import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react';
import SodaLyricsTab from './SodaLyricsTab';
import * as lyricsService from '../services/lyricsService';

vi.mock('../services/lyricsService', () => ({
  getMediaLyrics: vi.fn(),
  fetchMediaLyrics: vi.fn(),
}));

const getMock = lyricsService.getMediaLyrics as unknown as ReturnType<typeof vi.fn>;
const fetchMock = lyricsService.fetchMediaLyrics as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  getMock.mockReset();
  fetchMock.mockReset();
});
afterEach(cleanup);

describe('SodaLyricsTab — Fetch / Copy lyrics', () => {
  it('empty + qishui → shows Fetch Lyrics button', async () => {
    getMock.mockResolvedValue({ lrc: '', lines: [] });
    render(<SodaLyricsTab mediaId="m1" sourcePlatform="qishui" />);
    expect(await screen.findByText('Fetch Lyrics')).toBeInTheDocument();
  });

  it('empty + non-qishui → no Fetch button (no fetch path)', async () => {
    getMock.mockResolvedValue({ lrc: '', lines: [] });
    render(<SodaLyricsTab mediaId="m1" sourcePlatform="douyin" />);
    expect(await screen.findByText('No lyrics available')).toBeInTheDocument();
    expect(screen.queryByText('Fetch Lyrics')).not.toBeInTheDocument();
  });

  it('has lyrics → shows Copy Lyrics, no Fetch', async () => {
    getMock.mockResolvedValue({ lrc: '[00:00.00]Hi', lines: [{ text: 'Hi' }] });
    render(<SodaLyricsTab mediaId="m1" sourcePlatform="qishui" />);
    expect(await screen.findByText('Copy Lyrics')).toBeInTheDocument();
    expect(screen.queryByText('Fetch Lyrics')).not.toBeInTheDocument();
  });

  it('clicking Fetch Lyrics calls the service and renders the result', async () => {
    getMock.mockResolvedValue({ lrc: '', lines: [] });
    fetchMock.mockResolvedValue({ lrc: '[00:01.00]Hello', lines: [{ text: 'Hello' }] });
    render(<SodaLyricsTab mediaId="m1" sourcePlatform="qishui" />);

    const btn = await screen.findByText('Fetch Lyrics');
    fireEvent.click(btn);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('m1'));
    expect(await screen.findByText('Hello')).toBeInTheDocument();
  });

  it('Fetch returns empty → shows "No lyrics found" and hides the button', async () => {
    getMock.mockResolvedValue({ lrc: '', lines: [] });
    fetchMock.mockResolvedValue({ lrc: '', lines: [] });
    render(<SodaLyricsTab mediaId="m1" sourcePlatform="qishui" />);

    fireEvent.click(await screen.findByText('Fetch Lyrics'));

    expect(await screen.findByText('No lyrics found for this track')).toBeInTheDocument();
    expect(screen.queryByText('Fetch Lyrics')).not.toBeInTheDocument();
  });
});
