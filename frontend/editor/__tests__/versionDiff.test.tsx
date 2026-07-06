/**
 * VersionDiff tests (Phase B P4).
 *
 * Covers: the four change kinds (added / removed / changed / moved) each
 * rendering their before/after text, scene-set add/remove chips, the header
 * naming the commit against Current, empty-diff state, and the Back button.
 */
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { CommitDiff, ScriptCommit } from '../sceneService';
import type { SceneDoc } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const svc = vi.hoisted(() => ({ diffCommit: vi.fn() }));
vi.mock('../sceneService', () => svc);

import { VersionDiff } from '../versions/VersionDiff';

const commit: ScriptCommit = {
  id: '500',
  script_id: '1',
  message: 'First draft',
  watermarks: { '200': 3 },
  scene_ids: [],
  created_by: 'user-1',
  created_at: new Date().toISOString(),
};

const liveScenes: SceneDoc[] = [
  {
    id: '200',
    script_id: '1',
    chapter_id: null,
    heading_int_ext: 'INT',
    location_text: 'Kitchen',
    time_of_day: 'DAY',
    content_version: 5,
    sort_order: 0,
    elements: [],
  },
];

const fourKindDiff: CommitDiff = {
  scenes: [
    {
      scene_id: '200',
      elements: [
        { kind: 'added', id: 'el_a', before: null, after: { id: 'el_a', type: 'action', text: 'New line' } },
        { kind: 'removed', id: 'el_r', before: { id: 'el_r', type: 'action', text: 'Old line' }, after: null },
        {
          kind: 'changed',
          id: 'el_c',
          before: { id: 'el_c', type: 'dialogue', text: 'Before text' },
          after: { id: 'el_c', type: 'dialogue', text: 'After text' },
        },
        {
          kind: 'moved',
          id: 'el_m',
          before: { id: 'el_m', type: 'action', text: 'Moved line' },
          after: { id: 'el_m', type: 'action', text: 'Moved line' },
        },
      ],
    },
  ],
  scenes_added: [{ id: '201', sort_order: 1, heading_int_ext: 'EXT', location_text: 'Park' }],
  scenes_removed: [],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('VersionDiff', () => {
  it('renders all four change kinds with their before/after text', async () => {
    svc.diffCommit.mockResolvedValue(fourKindDiff);
    render(<VersionDiff scriptId="1" commit={commit} scenes={liveScenes} onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByTestId('version-diff')).toBeInTheDocument());
    expect(svc.diffCommit).toHaveBeenCalledWith('1', '500', 'current');

    const changes = await screen.findAllByTestId('diff-change');
    expect(changes).toHaveLength(4);
    const kinds = changes.map((c) => c.getAttribute('data-kind'));
    expect(kinds).toEqual(['added', 'removed', 'changed', 'moved']);

    // changed shows both before (struck) and after (highlit).
    expect(screen.getByText('Before text')).toBeInTheDocument();
    expect(screen.getByText('After text')).toBeInTheDocument();
    // added shows only after, removed shows only before.
    expect(screen.getByText('New line')).toBeInTheDocument();
    expect(screen.getByText('Old line')).toBeInTheDocument();
    // Both before-side texts render together.
    expect(screen.getAllByTestId('diff-before')).toHaveLength(2); // removed + changed
    expect(screen.getAllByTestId('diff-after')).toHaveLength(3); // added + changed + moved
  });

  it('renders scene-set additions and the header against Current', async () => {
    svc.diffCommit.mockResolvedValue(fourKindDiff);
    render(<VersionDiff scriptId="1" commit={commit} scenes={liveScenes} onBack={vi.fn()} />);

    expect(await screen.findByTestId('diff-scene-added')).toBeInTheDocument();
    expect(screen.getByText('First draft')).toBeInTheDocument();
    expect(screen.getByText('editor.diffCurrent')).toBeInTheDocument();
  });

  it('shows an empty state when nothing changed', async () => {
    svc.diffCommit.mockResolvedValue({ scenes: [], scenes_added: [], scenes_removed: [] });
    render(<VersionDiff scriptId="1" commit={commit} scenes={liveScenes} onBack={vi.fn()} />);
    expect(await screen.findByText('editor.diffNoChanges')).toBeInTheDocument();
  });

  it('shows an error state when the diff fails to load', async () => {
    svc.diffCommit.mockRejectedValue(new Error('boom'));
    render(<VersionDiff scriptId="1" commit={commit} scenes={liveScenes} onBack={vi.fn()} />);
    expect(await screen.findByText('editor.diffLoadError')).toBeInTheDocument();
  });

  it('calls onBack from the Back button', async () => {
    svc.diffCommit.mockResolvedValue(fourKindDiff);
    const onBack = vi.fn();
    render(<VersionDiff scriptId="1" commit={commit} scenes={liveScenes} onBack={onBack} />);
    await waitFor(() => expect(screen.getByTestId('version-diff')).toBeInTheDocument());
    fireEvent.click(screen.getByText('editor.diffBack'));
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});
