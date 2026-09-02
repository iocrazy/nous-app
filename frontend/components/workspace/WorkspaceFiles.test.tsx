/**
 * WorkspaceFiles (PR-10b Wave 2) — the unified Files module (folders +
 * uploaded files + renders, filtered by type chips).
 *
 * Pins: "All" merges folders + files + renders; "Media"/"Docs" filter the
 * files list by file_type; "Renders" consumes the /renders endpoint and
 * the current-episode toggle re-fetches with `episode_id`; double-clicking
 * a folder navigates in and updates the breadcrumb.
 */
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { WorkspaceFiles } from './WorkspaceFiles';
import type { EpisodeProgress, ProjectFile, ProjectFolder, RenderItem } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key.split('.').pop()!,
  }),
}));

vi.mock('../../utils/apiConfig', () => ({
  getApiUrl: () => 'https://api.test',
}));

vi.mock('../../utils/relativeTime', () => ({
  formatRelativeTime: () => '2h ago',
}));

const addToast = vi.fn();
vi.mock('../Toast', () => ({
  useToast: () => ({ addToast }),
}));

const mockService = vi.hoisted(() => ({
  fetchProjectFiles: vi.fn(),
  fetchProjectFolders: vi.fn(),
  fetchProjectRenders: vi.fn(),
  uploadFile: vi.fn(),
}));
vi.mock('../../services/projectsService', () => mockService);

function file(over: Partial<ProjectFile>): ProjectFile {
  return {
    id: 'f',
    project_id: 'p1',
    filename: 'file',
    file_type: null,
    mime_type: null,
    file_path: null,
    file_size_bytes: null,
    media_id: null,
    duration_seconds: null,
    resolution: null,
    fps: null,
    video_codec: null,
    audio_codec: null,
    video_bitrate_kbps: null,
    audio_bitrate_kbps: null,
    audio_channels: null,
    audio_sample_rate: null,
    thumbnail_path: null,
    cover_image_path: null,
    uploaded_by: null,
    notes: null,
    is_trashed: false,
    trashed_at: null,
    review_status: null,
    current_version: 1,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...over,
  };
}

function folder(over: Partial<ProjectFolder>): ProjectFolder {
  return {
    id: 'fold',
    project_id: 'p1',
    parent_id: null,
    name: 'Folder',
    created_by: null,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...over,
  };
}

const FOLDERS: ProjectFolder[] = [folder({ id: 'f1', name: 'References' })];
const FILES: ProjectFile[] = [
  file({ id: 'file1', filename: 'shot.png', file_type: 'image' }),
  file({ id: 'file2', filename: 'doc.pdf', file_type: 'document' }),
];
// generated_media ids are Snowflake BIGINTs stringified on the way out
// (`_normalize` in generated_media_repository) — NOT the tidy 'r1' this
// fixture used to carry. The difference is load-bearing: `mediaUrl`'s
// two-tier helpers only recognise `/generated-media/<digits>/cover`, so a
// made-up id makes them no-op and the test passes on a broken build.
const R_IMAGE = '7412093847562137600';
const R_VIDEO = '7412093847562137601';
const R_MORE = '7412093847562137602';
const RENDERS: RenderItem[] = [
  { id: R_IMAGE, media_kind: 'image', mime: 'image/png', origin_kind: 'shot_generate', node_id: 's1', created_at: '2026-07-08T00:00:00Z' },
  { id: R_VIDEO, media_kind: 'video', mime: 'video/mp4', origin_kind: 'shot_video', node_id: 's2', created_at: '2026-07-08T00:00:00Z' },
];

const EPISODE: EpisodeProgress = {
  episode_id: '1',
  title: 'Ep 1 — Pilot',
  sort_order: 10,
  script_count: 1,
  scene_count: 4,
  shots_total: 12,
  shots_done: 9,
  renders_count: 2,
  status: 'boarding',
};

beforeEach(() => {
  addToast.mockClear();
  mockService.fetchProjectFolders.mockReset().mockResolvedValue(FOLDERS);
  mockService.fetchProjectFiles.mockReset().mockResolvedValue(FILES);
  mockService.fetchProjectRenders.mockReset().mockResolvedValue({ items: RENDERS, next_cursor: null });
  mockService.uploadFile.mockReset().mockResolvedValue(file({ id: 'file3' }));
});

afterEach(() => cleanup());

describe('WorkspaceFiles', () => {
  it('"All" chip merges folders + files + renders', async () => {
    render(<WorkspaceFiles projectId="p1" currentEpisode={null} />);

    expect(await screen.findByTestId('ws-files-item-folder-f1')).toBeTruthy();
    expect(screen.getByTestId('ws-files-item-file-file1')).toBeTruthy();
    expect(screen.getByTestId('ws-files-item-file-file2')).toBeTruthy();
    expect(screen.getByTestId(`ws-files-item-render-${R_IMAGE}`)).toBeTruthy();
    expect(screen.getByTestId(`ws-files-item-render-${R_VIDEO}`)).toBeTruthy();
  });

  it('"Media" chip shows only image/video files', async () => {
    render(<WorkspaceFiles projectId="p1" currentEpisode={null} />);
    await screen.findByTestId('ws-files-item-file-file1');

    fireEvent.click(screen.getByTestId('ws-files-chip-media'));

    expect(screen.getByTestId('ws-files-item-file-file1')).toBeTruthy();
    expect(screen.queryByTestId('ws-files-item-file-file2')).toBeNull();
    expect(screen.queryByTestId('ws-files-item-folder-f1')).toBeNull();
  });

  it('"Docs" chip shows only non-media files', async () => {
    render(<WorkspaceFiles projectId="p1" currentEpisode={null} />);
    await screen.findByTestId('ws-files-item-file-file1');

    fireEvent.click(screen.getByTestId('ws-files-chip-docs'));

    expect(screen.getByTestId('ws-files-item-file-file2')).toBeTruthy();
    expect(screen.queryByTestId('ws-files-item-file-file1')).toBeNull();
  });

  it('"Renders" chip consumes the /renders endpoint exclusively', async () => {
    render(<WorkspaceFiles projectId="p1" currentEpisode={EPISODE} />);
    await screen.findByTestId('ws-files-item-file-file1');

    fireEvent.click(screen.getByTestId('ws-files-chip-renders'));

    expect(screen.getByTestId(`ws-files-item-render-${R_IMAGE}`)).toBeTruthy();
    expect(screen.getByTestId(`ws-files-item-render-${R_VIDEO}`)).toBeTruthy();
    expect(screen.queryByTestId('ws-files-item-file-file1')).toBeNull();
  });

  it('current-episode toggle re-fetches renders scoped to the episode', async () => {
    render(
      <WorkspaceFiles
        projectId="p1"
        currentEpisode={EPISODE}
        initialChip="renders"
        initialEpisodeFilterOn={false}
      />,
    );
    await waitFor(() =>
      expect(mockService.fetchProjectRenders).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ episodeId: null }),
      ),
    );

    fireEvent.click(screen.getByTestId('ws-files-ep-filter-toggle'));

    await waitFor(() =>
      expect(mockService.fetchProjectRenders).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ episodeId: '1' }),
      ),
    );
  });

  it('double-clicking a folder navigates in and updates the breadcrumb', async () => {
    render(<WorkspaceFiles projectId="p1" currentEpisode={null} />);
    const folderItem = await screen.findByTestId('ws-files-item-folder-f1');

    fireEvent.doubleClick(folderItem);

    await waitFor(() => expect(screen.getByTestId('ws-files-breadcrumb-f1')).toHaveTextContent('References'));
    await waitFor(() =>
      expect(mockService.fetchProjectFiles).toHaveBeenCalledWith('p1', false, 'f1'),
    );
  });

  it('renders pager appends the next page and hides once the cursor is exhausted', async () => {
    const MORE: RenderItem[] = [
      { id: R_MORE, media_kind: 'image', mime: 'image/png', origin_kind: 'shot_generate', node_id: 's3', created_at: '2026-07-09T00:00:00Z' },
    ];
    mockService.fetchProjectRenders
      .mockReset()
      .mockResolvedValueOnce({ items: RENDERS, next_cursor: 'cursor-1' })
      .mockResolvedValueOnce({ items: MORE, next_cursor: null });

    render(<WorkspaceFiles projectId="p1" currentEpisode={null} />);
    await screen.findByTestId(`ws-files-item-render-${R_IMAGE}`);

    fireEvent.click(await screen.findByTestId('ws-files-load-more'));

    // Next page is appended (page 1 stays, page 2 arrives) and the pager carried the cursor.
    expect(await screen.findByTestId(`ws-files-item-render-${R_MORE}`)).toBeTruthy();
    expect(screen.getByTestId(`ws-files-item-render-${R_IMAGE}`)).toBeTruthy();
    expect(mockService.fetchProjectRenders).toHaveBeenLastCalledWith(
      'p1',
      expect.objectContaining({ cursor: 'cursor-1' }),
    );
    // Exhausted cursor → pager disappears.
    await waitFor(() => expect(screen.queryByTestId('ws-files-load-more')).toBeNull());
  });

  it('does not show the pager when the first page has no next cursor', async () => {
    render(<WorkspaceFiles projectId="p1" currentEpisode={null} />);
    await screen.findByTestId(`ws-files-item-render-${R_IMAGE}`);
    expect(screen.queryByTestId('ws-files-load-more')).toBeNull();
  });

  it('double-clicking a file opens the FileInfoPanel preview drawer, backdrop closes it', async () => {
    render(<WorkspaceFiles projectId="p1" currentEpisode={null} />);
    const fileItem = await screen.findByTestId('ws-files-item-file-file1');

    expect(screen.queryByTestId('ws-files-preview')).toBeNull();
    fireEvent.doubleClick(fileItem);

    expect(await screen.findByTestId('ws-files-preview')).toBeTruthy();

    fireEvent.click(screen.getByTestId('ws-files-preview-backdrop'));
    await waitFor(() => expect(screen.queryByTestId('ws-files-preview')).toBeNull());
  });
  // ── Preview tier vs original (canvas fluency Task 1 regression) ──────────
  // `/generated-media/{id}/cover` answers with a 1024px WebP preview now and
  // only hands back the original for `?full=1`. This grid was left on the
  // bare url, so the thumbnail kept hitting a 7-day-immutable cache entry
  // holding the OLD full-size response, and "open in a new tab" quietly
  // started serving the preview instead of the file the user asked to see.

  it('opening a render in a new tab asks for the ORIGINAL', async () => {
    render(<WorkspaceFiles projectId="p1" currentEpisode={null} />);
    const row = await screen.findByTestId(`ws-files-item-render-${R_IMAGE}`);
    expect(row.getAttribute('href')).toContain('full=1');
  });

  it('the render thumbnail stays on the cache-busted preview tier', async () => {
    render(<WorkspaceFiles projectId="p1" currentEpisode={null} />);
    const row = await screen.findByTestId(`ws-files-item-render-${R_IMAGE}`);
    const thumb = row.querySelector('img');
    expect(thumb).toBeTruthy();
    expect(thumb!.getAttribute('src')).toContain('v=2');
    expect(thumb!.getAttribute('src')).not.toContain('full=1');
  });

  it('a video render keeps its stream url byte-for-byte', async () => {
    // Only `/cover` has two tiers; stamping either marker on `/stream` would
    // be a made-up query the endpoint never agreed to answer.
    render(<WorkspaceFiles projectId="p1" currentEpisode={null} />);
    const row = await screen.findByTestId(`ws-files-item-render-${R_VIDEO}`);
    expect(row.getAttribute('href')).toBe(
      `https://api.test/api/v1/generated-media/${R_VIDEO}/stream`,
    );
  });
});
