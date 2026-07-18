/**
 * VersionDiff tests (Cursor-style rewrite).
 *
 * Covers: the three change states (added / removed / changed-with-inline-word-
 * diff / moved), scene-set aggregation into a collapsible "Added N scenes"
 * group, author chips (server-resolved name + "You"), click-to-jump into the
 * live sheet, per-scene collapse, and the header / empty / error / Back paths.
 */
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react';
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
    elements: [
      { id: 'el_c', type: 'dialogue', text: 'After text' },
      { id: 'el_m', type: 'action', text: 'Moved line' },
    ],
  },
];

const fourKindDiff: CommitDiff = {
  scenes: [
    {
      scene_id: '200',
      author: 'u-bob',
      elements: [
        { kind: 'added', id: 'el_a', before: null, after: { id: 'el_a', type: 'action', text: 'New line' }, actor: 'u-bob' },
        { kind: 'removed', id: 'el_r', before: { id: 'el_r', type: 'action', text: 'Old line' }, after: null, actor: 'copilot' },
        {
          kind: 'changed',
          id: 'el_c',
          before: { id: 'el_c', type: 'dialogue', text: 'Before text' },
          after: { id: 'el_c', type: 'dialogue', text: 'After text' },
          actor: 'user-me',
        },
        {
          kind: 'moved',
          id: 'el_m',
          before: { id: 'el_m', type: 'action', text: 'Moved line' },
          after: { id: 'el_m', type: 'action', text: 'Moved line' },
          actor: 'u-bob',
        },
      ],
    },
  ],
  scenes_added: [{ id: '201', sort_order: 1, heading_int_ext: 'EXT', location_text: 'Park', author: 'u-bob' }],
  scenes_removed: [],
  authors: { 'u-bob': 'Bob' },
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('VersionDiff', () => {
  it('renders the four change kinds in order', async () => {
    svc.diffCommit.mockResolvedValue(fourKindDiff);
    render(<VersionDiff scriptId="1" commit={commit} scenes={liveScenes} onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByTestId('version-diff')).toBeInTheDocument());
    expect(svc.diffCommit).toHaveBeenCalledWith('1', '500', 'current');

    const changes = await screen.findAllByTestId('diff-change');
    expect(changes.map((c) => c.getAttribute('data-kind'))).toEqual([
      'added',
      'removed',
      'changed',
      'moved',
    ]);
    expect(screen.getAllByTestId('diff-before')).toHaveLength(2); // removed + changed
    expect(screen.getAllByTestId('diff-after')).toHaveLength(3); // added + changed + moved
  });

  it('shows a changed element as an inline word-level diff', async () => {
    svc.diffCommit.mockResolvedValue(fourKindDiff);
    render(<VersionDiff scriptId="1" commit={commit} scenes={liveScenes} onBack={vi.fn()} />);

    const changed = (await screen.findAllByTestId('diff-change')).find(
      (c) => c.getAttribute('data-kind') === 'changed',
    )!;
    // Old word struck (mh-w-del), new word highlit (mh-w-ins), shared word equal.
    expect(changed.querySelector('.mh-w-del')?.textContent).toBe('Before');
    expect(changed.querySelector('.mh-w-ins')?.textContent).toBe('After');
    expect(within(changed).getByTestId('diff-before').textContent).toBe('Before text');
    expect(within(changed).getByTestId('diff-after').textContent).toBe('After text');
  });

  it('aggregates scene-set additions into a collapsible group with a count', async () => {
    svc.diffCommit.mockResolvedValue(fourKindDiff);
    render(<VersionDiff scriptId="1" commit={commit} scenes={liveScenes} onBack={vi.fn()} />);

    const group = await screen.findByTestId('diff-sceneset-added');
    // The group head carries an aggregate count badge (1 here).
    expect(within(group).getByText('1')).toBeInTheDocument();
    // Small groups auto-expand → the scene item is visible.
    expect(screen.getByTestId('diff-scene-added')).toBeInTheDocument();
  });

  it('renders author chips: resolved name, "You", and AI', async () => {
    svc.diffCommit.mockResolvedValue(fourKindDiff);
    render(
      <VersionDiff
        scriptId="1"
        commit={commit}
        scenes={liveScenes}
        currentUserId="user-me"
        onBack={vi.fn()}
      />,
    );

    await screen.findAllByTestId('diff-change');
    const chips = screen.getAllByTestId('diff-author').map((c) => c.textContent);
    expect(chips).toContain('Bob'); // server-resolved
    expect(chips).toContain('editor.diffAuthorYou'); // current user (el_c)
    expect(chips).toContain('editor.diffAuthorAI'); // copilot (el_r)
  });

  it('jumps to the live element on click', async () => {
    svc.diffCommit.mockResolvedValue(fourKindDiff);
    const onJumpTo = vi.fn();
    render(
      <VersionDiff
        scriptId="1"
        commit={commit}
        scenes={liveScenes}
        onBack={vi.fn()}
        onJumpTo={onJumpTo}
      />,
    );

    const changed = (await screen.findAllByTestId('diff-change')).find(
      (c) => c.getAttribute('data-kind') === 'changed',
    )!;
    fireEvent.click(changed);
    // el_c exists in the live scene → jump to the element.
    expect(onJumpTo).toHaveBeenCalledWith('200', 'el_c');
  });

  it('collapses a scene section when its header is clicked', async () => {
    svc.diffCommit.mockResolvedValue(fourKindDiff);
    render(<VersionDiff scriptId="1" commit={commit} scenes={liveScenes} onBack={vi.fn()} />);

    await screen.findAllByTestId('diff-change');
    const sceneHead = screen.getByText('INT · Kitchen · DAY');
    fireEvent.click(sceneHead);
    await waitFor(() => expect(screen.queryAllByTestId('diff-change')).toHaveLength(0));
  });

  it('names the commit against Current in the header', async () => {
    svc.diffCommit.mockResolvedValue(fourKindDiff);
    render(<VersionDiff scriptId="1" commit={commit} scenes={liveScenes} onBack={vi.fn()} />);
    expect(await screen.findByText('First draft')).toBeInTheDocument();
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
