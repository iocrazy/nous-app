// components/resources/assets/sheet/SendAssetToCanvasDialog.test.tsx
//
// "Send To Canvas" (P4 Task 6) — pick a project, pick a canvas (or make one),
// and the asset lands there as a reference card.
//
// The canvas rows are the shape `canvases_router` sends: string ids. The
// failure cases each get their own line, because "could not send" for a
// read-only board, a lost optimistic-lock race and a dead request would leave
// the user with the same sentence and three different things to do.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Canvas } from '../../../../features/canvas-core/types';
import type { AssetRowDetail } from '../../../../services/assetsService';
import type { Project } from '../../../../types';

const listCanvases = vi.fn();
const createCanvas = vi.fn();
vi.mock('../../../../features/canvas-core/services/canvasService', () => ({
  listCanvases: (...a: unknown[]) => listCanvases(...a),
  createCanvas: (...a: unknown[]) => createCanvas(...a),
}));

const sendAssetToCanvas = vi.fn();
vi.mock('../../../../features/canvas-core/services/sendAssetToCanvas', () => ({
  sendAssetToCanvas: (...a: unknown[]) => sendAssetToCanvas(...a),
}));

const addToast = vi.fn();
vi.mock('../../../Toast', () => ({
  useToast: () => ({ addToast }),
  useOptionalToast: () => ({ addToast }),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

import { SendAssetToCanvasDialog } from './SendAssetToCanvasDialog';

const SCOPE = '727145299382534100';
const ASSET_ID = '727145299382534300';
const PROJECT_ID = '337610660408111';
const CANVAS_ID = '337610660408263';

const detail: AssetRowDetail = {
  id: ASSET_ID,
  scope_id: SCOPE,
  asset_type: 'character',
  subtype: null,
  name: 'Cole Bannon',
  role_tag: 'lead',
  description: '',
  attrs: {},
  prompt_positive: null,
  prompt_negative: null,
  prompt_positive_zh: null,
  prompt_negative_zh: null,
  platform_params: {},
  cover_file_id: null,
  source: 'manual',
  duplicated_from: null,
  is_system_preset: false,
  tags: {},
  sort_order: 0,
  created_by: null,
  created_at: '2026-09-01T00:00:00+00:00',
  updated_at: '2026-09-01T00:00:00+00:00',
  readiness: { state: 'ready', missing: [] },
  file_counts_by_slot: {},
  project_ids: [PROJECT_ID],
  loadout_count: 0,
  files: [],
  links: [],
  linked_by: [],
  loadouts: [],
  used_in: { canvases: [], storyboards: [] },
};

const project: Project = {
  id: PROJECT_ID,
  name: 'Bamboo Sea',
  description: null,
  owner_id: 'u1',
  team_id: SCOPE,
  project_type: 'internal',
  project_group: null,
  announcement: null,
  is_starred: false,
  color_label: null,
  archived_at: null,
  file_count: 0,
  created_at: '2026-09-01T00:00:00+00:00',
  updated_at: '2026-09-01T00:00:00+00:00',
};

const canvasRow = (over: Partial<Canvas> = {}): Canvas => ({
  id: CANVAS_ID,
  project_id: PROJECT_ID,
  name: 'Board One',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [],
  connections_json: [],
  node_ops_json: [],
  connection_ops_json: [],
  base_updated_at: '2026-09-02T10:00:00+00:00',
  created_at: '2026-09-01T00:00:00+00:00',
  updated_at: '2026-09-02T10:00:00+00:00',
  created_by: null,
  ...over,
});

const onDone = vi.fn();
const onClose = vi.fn();

function renderDialog(over: Record<string, unknown> = {}) {
  return render(
    <SendAssetToCanvasDialog
      detail={detail}
      loadoutId={null}
      projects={[project]}
      projectsLoading={false}
      projectsFailed={false}
      onClose={onClose}
      onDone={onDone}
      {...over}
    />,
  );
}

async function pickProjectAndCanvas() {
  fireEvent.click(screen.getByTestId('send-asset-project-option'));
  fireEvent.click(await screen.findByTestId('send-asset-canvas-option'));
}

beforeEach(() => {
  listCanvases.mockReset().mockResolvedValue([canvasRow()]);
  createCanvas.mockReset();
  sendAssetToCanvas
    .mockReset()
    .mockResolvedValue({ ok: true, canvasId: CANVAS_ID, nodeId: 'asset-1-abcd' });
  addToast.mockReset();
  onDone.mockReset();
  onClose.mockReset();
});

afterEach(cleanup);

describe('SendAssetToCanvasDialog', () => {
  it('picks a project, lists its canvases, and sends the asset to one', async () => {
    renderDialog();
    await pickProjectAndCanvas();
    await waitFor(() => expect(onDone).toHaveBeenCalled());

    expect(listCanvases).toHaveBeenCalledWith(PROJECT_ID);
    expect(sendAssetToCanvas).toHaveBeenCalledWith(CANVAS_ID, detail, {
      loadoutId: null,
    });
    // The node id is what the page turns into `?node=`, which centres the card.
    expect(onDone).toHaveBeenCalledWith(CANVAS_ID, 'asset-1-abcd');
  });

  it('binds the sheet’s active loadout to the card it sends', async () => {
    renderDialog({ loadoutId: '727145299382534401' });
    await pickProjectAndCanvas();
    await waitFor(() =>
      expect(sendAssetToCanvas).toHaveBeenCalledWith(CANVAS_ID, detail, {
        loadoutId: '727145299382534401',
      }),
    );
  });

  it('hides a retired classic canvas, which has no renderer to land on', async () => {
    listCanvases.mockResolvedValue([
      canvasRow({ id: '1', name: 'Old', kind: 'classic' }),
      canvasRow({ id: '2', name: 'Live', kind: 'smart' }),
    ]);
    renderDialog();
    fireEvent.click(screen.getByTestId('send-asset-project-option'));
    await waitFor(() =>
      expect(screen.getAllByTestId('send-asset-canvas-option')).toHaveLength(1),
    );
    expect(screen.getByTestId('send-asset-canvas-option')).toHaveTextContent('Live');
  });

  it('New Canvas creates a plain smart board and sends the card into it', async () => {
    createCanvas.mockResolvedValue(canvasRow({ id: '999', name: 'Cole Bannon' }));
    renderDialog();
    fireEvent.click(screen.getByTestId('send-asset-project-option'));
    fireEvent.click(await screen.findByTestId('send-asset-new-canvas'));
    await waitFor(() => expect(onDone).toHaveBeenCalled());

    // NOT the entity canvas — that is what "Open In Canvas" is for, and
    // offering it twice would make the two buttons indistinguishable.
    expect(createCanvas).toHaveBeenCalledWith(PROJECT_ID, {
      name: 'Cole Bannon',
      kind: 'smart',
    });
    expect(sendAssetToCanvas).toHaveBeenCalledWith('999', detail, { loadoutId: null });
  });

  it.each([
    ['read_only', 'You can only read that canvas'],
    ['conflict', 'Someone else is editing that canvas. Try again.'],
    ['load_failed', 'Could not open that canvas'],
    ['save_failed', 'Could not add the card to that canvas'],
  ])('reports the %s refusal in its own words', async (reason, message) => {
    sendAssetToCanvas.mockResolvedValue({ ok: false, reason, message: 'x' });
    renderDialog();
    await pickProjectAndCanvas();
    await waitFor(() => expect(addToast).toHaveBeenCalledWith(message, 'error'));
    expect(onDone).not.toHaveBeenCalled();
  });

  it('reports a failed CREATE without trying to send into nothing', async () => {
    createCanvas.mockRejectedValue(new Error('403'));
    renderDialog();
    fireEvent.click(screen.getByTestId('send-asset-project-option'));
    fireEvent.click(await screen.findByTestId('send-asset-new-canvas'));
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('Could not create the canvas', 'error'),
    );
    expect(sendAssetToCanvas).not.toHaveBeenCalled();
  });

  it('states an empty project apart from a failed list', async () => {
    listCanvases.mockResolvedValue([]);
    renderDialog();
    fireEvent.click(screen.getByTestId('send-asset-project-option'));
    expect(await screen.findByText('This Project Has No Canvases Yet')).toBeTruthy();
    // New Canvas is still offered — an empty project is exactly where it helps.
    expect(screen.getByTestId('send-asset-new-canvas')).toBeTruthy();
  });

  it('says a failed canvas list is a failure, not an empty project', async () => {
    listCanvases.mockRejectedValue(new Error('500'));
    renderDialog();
    fireEvent.click(screen.getByTestId('send-asset-project-option'));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load the canvases here');
  });

  it('keeps the three project states apart', async () => {
    const { rerender } = renderDialog({ projectsLoading: true, projects: [] });
    expect(screen.getByText('Loading...')).toBeTruthy();

    rerender(
      <SendAssetToCanvasDialog
        detail={detail}
        loadoutId={null}
        projects={[]}
        projectsLoading={false}
        projectsFailed
        onClose={onClose}
        onDone={onDone}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Projects unavailable');

    rerender(
      <SendAssetToCanvasDialog
        detail={detail}
        loadoutId={null}
        projects={[]}
        projectsLoading={false}
        projectsFailed={false}
        onClose={onClose}
        onDone={onDone}
      />,
    );
    expect(screen.getByText('Create A Project First')).toBeTruthy();
  });
});
