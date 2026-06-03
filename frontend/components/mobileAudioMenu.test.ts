import { describe, it, expect } from 'vitest';
import type { Video } from '../types';
import { audioMenuActions } from './mobileAudioMenu';

/** Minimal Video stub — only the fields the menu logic reads. Status fields
 * are lowercase at runtime (the helper lowercases), so build loosely-typed. */
function v(over: Record<string, unknown> = {}): Video {
  return { id: '1', source_platform: 'qishui', ...over } as unknown as Video;
}

describe('audioMenuActions — per-asset (mirrors PC DownloadMenuDropdown)', () => {
  it('audio present → Download Audio', () => {
    const items = audioMenuActions(v(), { hasAudio: true, isQishui: true });
    expect(items).toContainEqual({ asset: 'audio', kind: 'download' });
  });

  it('audio missing on qishui → Fetch Audio', () => {
    const items = audioMenuActions(v(), { hasAudio: false, isQishui: true });
    expect(items).toContainEqual({ asset: 'audio', kind: 'fetch' });
  });

  it('audio missing + status failed on qishui → Retry Audio', () => {
    const items = audioMenuActions(
      v({ music_download_status: 'failed' }),
      { hasAudio: false, isQishui: true },
    );
    expect(items).toContainEqual({ asset: 'audio', kind: 'retry' });
  });

  it('non-qishui with no audio → no audio fetch item (screen has no generic path)', () => {
    const items = audioMenuActions(v({ source_platform: 'douyin' }), { hasAudio: false, isQishui: false });
    expect(items.find((i) => i.asset === 'audio')).toBeUndefined();
  });

  it('cover completed + file → Download Cover', () => {
    const items = audioMenuActions(
      v({ cover_download_status: 'completed', cover_download_path: 'global/x/cover.jpg' }),
      { hasAudio: true, isQishui: true },
    );
    expect(items).toContainEqual({ asset: 'cover', kind: 'download' });
  });

  it('cover status completed but NO file → treat as fetchable (qishui)', () => {
    const items = audioMenuActions(
      v({ cover_download_status: 'completed' }),
      { hasAudio: true, isQishui: true },
    );
    expect(items).toContainEqual({ asset: 'cover', kind: 'fetch' });
  });

  it('THE BUG CASE — audio downloaded but cover missing (qishui): offer Fetch Cover', () => {
    const items = audioMenuActions(v(), { hasAudio: true, isQishui: true });
    expect(items).toContainEqual({ asset: 'audio', kind: 'download' });
    expect(items).toContainEqual({ asset: 'cover', kind: 'fetch' });
  });

  it('cover failed on qishui → Retry Cover', () => {
    const items = audioMenuActions(
      v({ cover_download_status: 'failed' }),
      { hasAudio: true, isQishui: true },
    );
    expect(items).toContainEqual({ asset: 'cover', kind: 'retry' });
  });

  it('non-qishui cover missing → no cover fetch item', () => {
    const items = audioMenuActions(v({ source_platform: 'douyin' }), { hasAudio: true, isQishui: false });
    expect(items.find((i) => i.asset === 'cover')).toBeUndefined();
  });
});
