import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { DownloadMenuDropdown } from './DownloadMenuDropdown';
import type { Video } from '../../types';

afterEach(cleanup);

function renderMenu(over: Record<string, unknown>, onFetchSodaAudio?: () => void) {
  const video = { id: '1', source_platform: 'qishui', ...over } as unknown as Video;
  return render(
    <DownloadMenuDropdown
      video={video}
      isDownloading={false}
      isFetching={false}
      onClose={() => {}}
      onDownload={() => {}}
      onFetchMedia={() => {}}
      onExtractAudio={() => {}}
      onFetchSodaAudio={onFetchSodaAudio}
    />,
  );
}

describe('DownloadMenuDropdown — qishui cover parity', () => {
  it('qishui audio track w/ downloaded audio but missing cover → offers Fetch Cover', () => {
    renderMenu({ music_download_path: 'global/x/audio.mp3' }, vi.fn());
    expect(screen.getByText('Audio')).toBeInTheDocument();
    expect(screen.getByText('Fetch Cover')).toBeInTheDocument();
  });

  it('qishui cover failed → Retry Cover', () => {
    renderMenu(
      { music_download_path: 'global/x/audio.mp3', cover_download_status: 'failed' },
      vi.fn(),
    );
    expect(screen.getByText('Retry Cover')).toBeInTheDocument();
  });

  it('qishui cover completed (with file) → plain Cover download, no Fetch', () => {
    renderMenu(
      {
        music_download_path: 'global/x/audio.mp3',
        cover_download_status: 'completed',
        cover_download_path: 'global/x/cover.jpg',
      },
      vi.fn(),
    );
    expect(screen.getByText('Cover')).toBeInTheDocument();
    expect(screen.queryByText('Fetch Cover')).not.toBeInTheDocument();
  });

  it('no onFetchSodaAudio handler → no qishui cover button (avoids dead action)', () => {
    renderMenu({ music_download_path: 'global/x/audio.mp3' }, undefined);
    expect(screen.queryByText('Fetch Cover')).not.toBeInTheDocument();
  });
});
