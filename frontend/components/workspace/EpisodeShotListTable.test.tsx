/**
 * EpisodeShotListTable (三视图 Task 2) — the "Shot List" view's flat table:
 * one group-header row per scene (colSpan=7, shown even for zero-shot
 * scenes) followed by one row per shot, plus a CSV export button that
 * shells out to buildShotListCsv + a Blob/object-URL anchor download
 * (zipExport.ts:102 pattern).
 */
import { createRef } from 'react';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Shot } from '../../editor/sceneService';
import type { SceneDoc } from '../../editor/types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const svc = vi.hoisted(() => ({
  listScenes: vi.fn(),
  listShots: vi.fn(),
}));
vi.mock('../../editor/sceneService', () => svc);

import { EpisodeShotListTable, type EpisodeShotListTableHandle } from './EpisodeShotListTable';

const scene = (over: Partial<SceneDoc> = {}): SceneDoc => ({
  id: '200',
  script_id: '1',
  chapter_id: null,
  scene_number: null,
  heading_int_ext: 'INT',
  location_text: 'Kitchen',
  time_of_day: 'DAY',
  content_version: 1,
  sort_order: 0,
  elements: [{ id: 'el_1', type: 'action', text: 'She enters.' }],
  ...over,
});

const shot = (over: Partial<Shot> = {}): Shot => ({
  id: '900',
  scene_id: '200',
  shot_number: 1,
  shot_type: 'WIDE',
  camera_angle: 'EYE',
  camera_movement: 'STATIC',
  focal_length: '35mm',
  lighting: null,
  description: 'Establishing shot of the kitchen.',
  image_url: null,
  thumbnail_url: null,
  video_url: null,
  status: 'empty',
  sort_order: 1000,
  ...over,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('EpisodeShotListTable', () => {
  it('renders a group header row per scene and a row per shot', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: '200' }), scene({ id: '201' })]);
    svc.listShots.mockImplementation(async (sceneId: string) =>
      sceneId === '200' ? [shot({ id: '900' }), shot({ id: '901', shot_number: 2 })] : [shot({ id: '902', scene_id: '201' })],
    );
    render(<EpisodeShotListTable scriptId="1" />);

    await waitFor(() => {
      expect(screen.getByTestId('ep-shotlist-group-200')).toBeInTheDocument();
      expect(screen.getByTestId('ep-shotlist-group-201')).toBeInTheDocument();
    });
    expect(screen.getByTestId('ep-shotlist-row-900')).toBeInTheDocument();
    expect(screen.getByTestId('ep-shotlist-row-901')).toBeInTheDocument();
    expect(screen.getByTestId('ep-shotlist-row-902')).toBeInTheDocument();
  });

  it('group header carries scene metadata, shot count, and colSpan=7', async () => {
    svc.listScenes.mockResolvedValue([
      scene({ id: '200', scene_number: '7A', heading_int_ext: 'EXT', location_text: 'Yard', time_of_day: 'NIGHT' }),
    ]);
    svc.listShots.mockResolvedValue([shot({ id: '900' }), shot({ id: '901', shot_number: 2 })]);
    render(<EpisodeShotListTable scriptId="1" />);

    await waitFor(() => expect(screen.getByTestId('ep-shotlist-group-200')).toBeInTheDocument());
    const groupRow = screen.getByTestId('ep-shotlist-group-200');
    expect(groupRow).toHaveTextContent('7A');
    expect(groupRow).toHaveTextContent('EXT');
    expect(groupRow).toHaveTextContent('Yard');
    expect(groupRow).toHaveTextContent('NIGHT');
    expect(groupRow).toHaveTextContent('(2)');
    const cell = groupRow.querySelector('td');
    expect(cell).toHaveAttribute('colspan', '7');
  });

  it('still emits a group header for a scene with zero shots, showing (0)', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: '200' })]);
    svc.listShots.mockResolvedValue([]);
    render(<EpisodeShotListTable scriptId="1" />);

    await waitFor(() => expect(screen.getByTestId('ep-shotlist-group-200')).toBeInTheDocument());
    expect(screen.getByTestId('ep-shotlist-group-200')).toHaveTextContent('(0)');
    expect(screen.queryByTestId(/ep-shotlist-row-/)).not.toBeInTheDocument();
  });

  it('renders the shot mono badge as {scene}-{shot} and the seven data columns', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: '200', scene_number: '3' })]);
    svc.listShots.mockResolvedValue([shot({ id: '900', shot_number: 5 })]);
    render(<EpisodeShotListTable scriptId="1" />);

    await waitFor(() => expect(screen.getByTestId('ep-shotlist-row-900')).toBeInTheDocument());
    const row = screen.getByTestId('ep-shotlist-row-900');
    expect(row).toHaveTextContent('3-5');
    expect(row).toHaveTextContent('WIDE');
    expect(row).toHaveTextContent('EYE');
    expect(row).toHaveTextContent('STATIC');
    expect(row).toHaveTextContent('35mm');
    expect(row).toHaveTextContent('Establishing shot of the kitchen.');
  });

  it('clicking export builds a CSV blob and downloads it via an object-URL anchor', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: '200', scene_number: '1' })]);
    svc.listShots.mockResolvedValue([shot({ id: '900' })]);
    const createSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock');
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    render(<EpisodeShotListTable scriptId="42" />);
    await waitFor(() => expect(screen.getByTestId('ep-shotlist-row-900')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('ep-shotlist-export'));

    expect(createSpy).toHaveBeenCalledTimes(1);
    const blobArg = createSpy.mock.calls[0][0] as Blob;
    expect(blobArg.type).toMatch(/text\/csv/);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeSpy).toHaveBeenCalledWith('blob:mock');
  });

  // Task 3 (主工作面接线): the Export trigger moves to EpisodeViewTabs' actions
  // slot when the table is embedded in ProjectWorkspace — `hideExport`
  // suppresses the built-in button, and the parent drives export through the
  // forwarded ref instead (no second scenes/shots fetch at the parent).
  it('hideExport suppresses the built-in Export button', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: '200' })]);
    svc.listShots.mockResolvedValue([shot({ id: '900' })]);
    render(<EpisodeShotListTable scriptId="1" hideExport />);

    await waitFor(() => expect(screen.getByTestId('ep-shotlist-row-900')).toBeInTheDocument());
    expect(screen.queryByTestId('ep-shotlist-export')).toBeNull();
  });

  it('exposes exportCsv on the forwarded ref, producing the same CSV blob as the built-in button', async () => {
    svc.listScenes.mockResolvedValue([scene({ id: '200', scene_number: '1' })]);
    svc.listShots.mockResolvedValue([shot({ id: '900' })]);
    const createSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const ref = createRef<EpisodeShotListTableHandle>();

    render(<EpisodeShotListTable ref={ref} scriptId="42" hideExport />);
    await waitFor(() => expect(screen.getByTestId('ep-shotlist-row-900')).toBeInTheDocument());

    expect(screen.queryByTestId('ep-shotlist-export')).toBeNull();
    ref.current?.exportCsv();

    expect(createSpy).toHaveBeenCalledTimes(1);
    const blobArg = createSpy.mock.calls[0][0] as Blob;
    expect(blobArg.type).toMatch(/text\/csv/);
  });
});
