import { fireEvent, render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { FileCard } from './FileCard';
import type { ProjectFile } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    // Two call shapes: `t(key, { id })` for the interpolated chip label, and
    // `t(key, 'English fallback')` for copy whose default lives at the call
    // site. Returning the fallback (not the key) is what lets an assertion
    // name the SENTENCE a reader sees.
    t: (key: string, opts?: Record<string, unknown> | string) => {
      if (typeof opts === 'string') return opts;
      return opts && 'id' in opts ? `from ${opts.id}` : key;
    },
  }),
}));

const baseFile: ProjectFile = {
  id: '900',
  project_id: '500',
  filename: 'final-cut.mp4',
  file_type: 'video',
  mime_type: 'video/mp4',
  file_path: '/x',
  file_size_bytes: 1024,
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
  created_at: '2026-07-20T00:00:00Z',
  updated_at: '2026-07-20T00:00:00Z',
};

function renderCard(file: ProjectFile, viewMode: 'grid' | 'list' = 'grid') {
  return render(
    <MemoryRouter initialEntries={['/team/77/projects/500']}>
      <Routes>
        <Route
          path="/team/:teamId/projects/:projectId"
          element={<FileCard file={file} onClick={() => {}} viewMode={viewMode} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('FileCard source-issue chip', () => {
  it('renders a "from MH-xx" chip linking to the issue when filed from one', () => {
    renderCard({ ...baseFile, source_issue_identifier: 'MH-42' });
    const chip = screen.getByTestId('file-source-issue-chip');
    expect(chip).toHaveTextContent('from MH-42');
    expect(chip).toHaveAttribute('href', '/team/77/todolist/MH-42');
  });

  it('renders no chip when the file has no source issue', () => {
    renderCard(baseFile);
    expect(screen.queryByTestId('file-source-issue-chip')).toBeNull();
  });

  it('renders the chip in list mode too', () => {
    renderCard({ ...baseFile, source_issue_identifier: 'MH-7' }, 'list');
    expect(screen.getByTestId('file-source-issue-chip')).toHaveTextContent('from MH-7');
  });
});

describe('FileCard source-issue chip — no team, no link (B7)', () => {
  it('shows where the file came from but offers no dead click', () => {
    // `router.tsx` registers `todolist/:identifier` under `/team/:teamId` and
    // nowhere else, so the old team-less `/todolist/MH-42` fallback was a
    // link to a route that does not exist. The backend's one builder refuses
    // the same case (`issue_links.py`: no team → None → disabled control).
    render(
      <MemoryRouter initialEntries={['/projects/500']}>
        <Routes>
          <Route
            path="/projects/:projectId"
            element={<FileCard file={{ ...baseFile, source_issue_identifier: 'MH-42' }} onClick={() => {}} viewMode="grid" />}
          />
        </Routes>
      </MemoryRouter>,
    );
    const chip = screen.getByTestId('file-source-issue-chip');
    expect(chip).toHaveTextContent('from MH-42');
    expect(chip).not.toHaveAttribute('href');
    // The tooltip says WHY, rather than leaving an inert control unexplained
    // (评审 L3). The sentence, not the key.
    expect(chip).toHaveAttribute('title', 'No team context — open the issue from its board');
  });

  it('swallows the click instead of letting it open the file', () => {
    // The Link stopped propagation; the span must too, or clicking the
    // provenance chip silently becomes "open this file".
    const onClick = vi.fn();
    render(
      <MemoryRouter initialEntries={['/projects/500']}>
        <Routes>
          <Route
            path="/projects/:projectId"
            element={<FileCard file={{ ...baseFile, source_issue_identifier: 'MH-42' }} onClick={onClick} viewMode="grid" />}
          />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTestId('file-source-issue-chip'));
    expect(onClick).not.toHaveBeenCalled();
  });
});
