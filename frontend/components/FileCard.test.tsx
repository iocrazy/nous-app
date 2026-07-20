import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { FileCard } from './FileCard';
import type { ProjectFile } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) =>
      opts && 'id' in opts ? `from ${opts.id}` : key,
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
