import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const navigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => navigate,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

const fetchProjects = vi.fn();
vi.mock('../../services/projectsService', () => ({
  fetchProjects: (...a: unknown[]) => fetchProjects(...a),
}));

const listCanvases = vi.fn();
vi.mock('../../features/canvas-core/services/canvasService', () => ({
  listCanvases: (...a: unknown[]) => listCanvases(...a),
}));

vi.mock('../../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `https://api.test/cover/${id}`,
}));

import { SendToCanvasModal } from './SendToCanvasModal';
import type { Resource } from '../../types';

const resource = { id: 'r1', filename: 'hero.png' } as unknown as Resource;

beforeEach(() => {
  navigate.mockReset();
  fetchProjects.mockReset();
  listCanvases.mockReset();
});

describe('SendToCanvasModal', () => {
  it('picks a project then a canvas, navigating with a full promptInsert state', async () => {
    fetchProjects.mockResolvedValue([
      { id: 'p1', name: 'Project One', team_id: 't1' },
      { id: 'p2', name: 'Project Two', team_id: null },
    ]);
    listCanvases.mockResolvedValue([{ id: 'c1', name: 'Board One' }]);

    render(
      <SendToCanvasModal
        resource={resource}
        positive="a cinematic hero shot"
        negative={null}
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByText('Project One')).toBeTruthy();
    fireEvent.click(screen.getByText('Project One'));

    await waitFor(() => expect(listCanvases).toHaveBeenCalledWith('p1'));
    fireEvent.click(await screen.findByText('Board One'));

    expect(navigate).toHaveBeenCalledWith('/team/t1/canvas/c1', {
      state: {
        promptInsert: {
          assetId: 'r1',
          filename: 'hero.png',
          positive: 'a cinematic hero shot',
          negative: undefined,
          coverUrl: 'https://api.test/cover/r1',
        },
      },
    });
  });

  it('navigates without a team prefix for a personal (team_id null) project, keeping negative text', async () => {
    fetchProjects.mockResolvedValue([{ id: 'p2', name: 'Personal Proj', team_id: null }]);
    listCanvases.mockResolvedValue([{ id: 'c2', name: 'Solo Board' }]);

    render(
      <SendToCanvasModal resource={resource} positive="p" negative="bad hands" onClose={vi.fn()} />,
    );

    fireEvent.click(await screen.findByText('Personal Proj'));
    fireEvent.click(await screen.findByText('Solo Board'));

    expect(navigate).toHaveBeenCalledWith(
      '/canvas/c2',
      expect.objectContaining({
        state: { promptInsert: expect.objectContaining({ negative: 'bad hands' }) },
      }),
    );
  });

  it('shows an empty state and no canvases panel when the account has no projects', async () => {
    fetchProjects.mockResolvedValue([]);
    render(<SendToCanvasModal resource={resource} positive="p" negative={null} onClose={vi.fn()} />);

    expect(await screen.findByText(/No projects yet/)).toBeTruthy();
    expect(listCanvases).not.toHaveBeenCalled();
  });

  it('shows an empty state when the picked project has no canvases', async () => {
    fetchProjects.mockResolvedValue([{ id: 'p1', name: 'Empty Proj', team_id: 't1' }]);
    listCanvases.mockResolvedValue([]);
    render(<SendToCanvasModal resource={resource} positive="p" negative={null} onClose={vi.fn()} />);

    fireEvent.click(await screen.findByText('Empty Proj'));
    expect(await screen.findByText(/no canvases yet/i)).toBeTruthy();
  });

  it('excludes classic-kind canvases from the list', async () => {
    fetchProjects.mockResolvedValue([{ id: 'p1', name: 'Proj', team_id: null }]);
    listCanvases.mockResolvedValue([
      { id: 'c1', name: 'Regular Board', kind: 'whiteboard' },
      { id: 'c2', name: 'Classic Board', kind: 'classic' },
      { id: 'c3', name: 'Another Board', kind: 'whiteboard' },
    ]);

    render(<SendToCanvasModal resource={resource} positive="p" negative={null} onClose={vi.fn()} />);

    fireEvent.click(await screen.findByText('Proj'));
    await waitFor(() => expect(listCanvases).toHaveBeenCalled());

    // Should show both non-classic canvases
    expect(await screen.findByText('Regular Board')).toBeTruthy();
    expect(screen.getByText('Another Board')).toBeTruthy();
    // Should NOT show the classic canvas
    expect(screen.queryByText('Classic Board')).toBeNull();
  });

  it('calls onClose on backdrop click', async () => {
    fetchProjects.mockResolvedValue([]);
    const onClose = vi.fn();
    render(<SendToCanvasModal resource={resource} positive="p" negative={null} onClose={onClose} />);
    await screen.findByText(/No projects yet/);

    fireEvent.click(screen.getByTestId('send-to-canvas-backdrop'));
    expect(onClose).toHaveBeenCalled();
  });
});
