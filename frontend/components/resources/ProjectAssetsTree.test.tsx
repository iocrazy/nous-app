/**
 * ProjectAssetsTree — New Canvas creation entry (PR-B).
 *
 * The `createCanvas` API had zero UI callers since #599; canvases could only
 * be listed, never created. This wires a New Canvas entry on each project row
 * (and in the per-project empty state) that calls createCanvas and navigates
 * straight into the editor. The freshly created canvas is empty, so the
 * orphan filter (batch A) hides it from THIS tree until the user adds a node —
 * hence the flow is create -> navigate, deliberately NOT create -> refresh.
 */

import React from 'react';
import { render, screen, fireEvent, waitFor, cleanup, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const navigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => navigate,
  useParams: () => ({ teamId: 't1' }),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const addToast = vi.fn();
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));

const fetchProjectAssetsTree = vi.fn();
vi.mock('../../services/projectAssetsService', () => ({
  fetchProjectAssetsTree: () => fetchProjectAssetsTree(),
}));

const createCanvas = vi.fn();
vi.mock('../../features/canvas-core/services/canvasService', () => ({
  createCanvas: (projectId: string, payload: unknown) => createCanvas(projectId, payload),
}));

import { ProjectAssetsTree } from './ProjectAssetsTree';

const noop = () => {};
const baseProps = {
  selection: { kind: 'chat-uploads' } as const,
  onSelect: noop,
  chatUploadsCount: 0,
};

beforeEach(() => {
  navigate.mockReset();
  addToast.mockReset();
  createCanvas.mockReset();
  fetchProjectAssetsTree.mockReset();
});

afterEach(() => cleanup());

describe('ProjectAssetsTree New Canvas entry', () => {
  it('opens the name dialog and creates through it (IC-style)', async () => {
    fetchProjectAssetsTree.mockResolvedValue([
      {
        project_id: 'proj-1',
        name: 'Proj One',
        canvases: [
          { canvas_id: 'c9', canvas_name: 'Board', kind: 'smart', asset_count: 2 },
        ],
      },
    ]);
    createCanvas.mockResolvedValue({ id: 'c-new', project_id: 'proj-1', name: 'Untitled Canvas' });

    render(<ProjectAssetsTree {...baseProps} />);

    // Project with canvases auto-expands; the row's New Canvas button now
    // opens the shared NewCanvasDialog instead of creating immediately.
    const btn = await screen.findByRole('button', { name: 'projectAssets.newCanvas' });
    fireEvent.click(btn);
    expect(createCanvas).not.toHaveBeenCalled();

    const dialog = await screen.findByRole('dialog');
    // The dialog is name-only now (classic is retired); Create submits and
    // the backend defaults the kind to smart.
    fireEvent.click(within(dialog).getByRole('button', { name: 'common.create' }));

    await waitFor(() => expect(createCanvas).toHaveBeenCalledTimes(1));
    expect(createCanvas).toHaveBeenCalledWith('proj-1', {
      name: 'projectAssets.untitledCanvas',
    });
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith('/team/t1/canvas/c-new'),
    );
    expect(addToast).not.toHaveBeenCalled();
  });

  it('offers the dialog from the empty (no-canvas) project state', async () => {
    fetchProjectAssetsTree.mockResolvedValue([
      { project_id: 'proj-2', name: 'Empty Proj', canvases: [] },
    ]);
    createCanvas.mockResolvedValue({ id: 'c-empty-new', project_id: 'proj-2', name: 'Untitled Canvas' });

    render(<ProjectAssetsTree {...baseProps} />);

    // Empty projects don't auto-expand — open it to reveal the empty state.
    const projectToggle = await screen.findByRole('button', { name: /Empty Proj/ });
    fireEvent.click(projectToggle);

    const entries = await screen.findAllByRole('button', { name: 'projectAssets.newCanvas' });
    fireEvent.click(entries[entries.length - 1]);

    const dialog = await screen.findByRole('dialog');
    // Just hit Create — the backend defaults the kind to smart.
    fireEvent.click(within(dialog).getByRole('button', { name: 'common.create' }));

    await waitFor(() =>
      expect(createCanvas).toHaveBeenCalledWith('proj-2', {
        name: 'projectAssets.untitledCanvas',
      }),
    );
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/team/t1/canvas/c-empty-new'));
  });

  it('toasts and does not navigate when creation fails', async () => {
    fetchProjectAssetsTree.mockResolvedValue([
      {
        project_id: 'proj-1',
        name: 'Proj One',
        canvases: [
          { canvas_id: 'c9', canvas_name: 'Board', kind: 'smart', asset_count: 2 },
        ],
      },
    ]);
    createCanvas.mockRejectedValue(new Error('boom'));

    render(<ProjectAssetsTree {...baseProps} />);
    const btn = await screen.findByRole('button', { name: 'projectAssets.newCanvas' });
    fireEvent.click(btn);
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'common.create' }));

    await waitFor(() => expect(addToast).toHaveBeenCalledWith('projectAssets.createFailed', 'error'));
    expect(navigate).not.toHaveBeenCalled();
  });
});
