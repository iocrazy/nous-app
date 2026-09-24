/**
 * The by-id / create / single-file endpoints return narrower rows than the
 * list endpoints; these lifts are the one place that bridges them.
 */
import { describe, expect, it, vi } from 'vitest';
import type { ProjectDetail, ProjectFileRow } from '../types/api';
import { toProject, toProjectFile } from './projectsService';
import { makeProject } from '../tests/fixtures/projects';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

describe('toProject', () => {
  it('keeps the detail row and nulls the list-only card enrichment', () => {
    // Spread of a list row is a structural superset of ProjectDetail; the
    // enrichment keys it carries (all null here) are what toProject resets.
    const detail: ProjectDetail = { ...makeProject({ id: 501, file_count: 4 }), effective_role: 'viewer' };

    const project = toProject(detail);

    expect(project.id).toBe(501);
    expect(project.file_count).toBe(4);
    expect(project.members_preview).toBeNull();
    expect(project.workflow_badge).toBeNull();
    expect(project.latest_activity).toBeNull();
  });
});

describe('toProjectFile', () => {
  const row: ProjectFileRow = {
    id: 9001, project_id: 501, folder_id: null, filename: 'cut.mp4', file_type: 'video',
    mime_type: 'video/mp4', file_path: null, file_size_bytes: null, media_id: null,
    duration_seconds: null, resolution: null, fps: null, video_codec: null, audio_codec: null,
    video_bitrate_kbps: null, audio_bitrate_kbps: null, audio_channels: null,
    audio_sample_rate: null, thumbnail_path: null, cover_image_path: null, uploaded_by: null,
    notes: null, is_trashed: false, trashed_at: null, review_status: null, current_version: 2,
    created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
    source_issue_id: 337610660408111,
  };

  it('stringifies source_issue_id and keeps the caller-held identifier', () => {
    const file = toProjectFile(row, { ...row, source_issue_id: '337610660408111', source_issue_identifier: 'MH-7' });
    expect(file.source_issue_id).toBe('337610660408111');
    expect(file.source_issue_identifier).toBe('MH-7');
    expect(file.current_version).toBe(2);
  });
});
