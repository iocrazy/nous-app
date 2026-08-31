/**
 * The sheet as a page: the skeleton, the three body substitutions, the two
 * counts-contract mutations, and read-only mode.
 *
 * `refreshAssetCounts` after a delete or duplicate is a CONTRACT, not a
 * nicety: nothing else moves the six sidebar badges, so skipping it leaves the
 * rail stating a number that is no longer true, indefinitely, with no error
 * anywhere. Task 6 wired it for create; these two are the rest of it.
 *
 * The fixtures are the real `AssetDetailResponse` shape - string ids
 * throughout, `scope_id: null` only on the preset, sparse
 * `file_counts_by_slot`, `tags` as an object of group to values.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { i18nMock, resPath, SCOPE_ID, TEAM_ID, UiModalStub } from './sheetTestUtils';

vi.mock('react-i18next', () => i18nMock);
vi.mock('../../../ui/primitives', () => ({ UiModal: UiModalStub }));

const navigate = vi.fn();
vi.mock('react-router-dom', () => ({ useNavigate: () => navigate }));

const addToast = vi.fn();
vi.mock('../../../Toast', () => ({ useToast: () => ({ addToast }) }));

const refreshAssetCounts = vi.fn();
vi.mock('../../../../contexts/ResourcesContext', () => ({
  useResourcesContext: () => ({
    scopeId: SCOPE_ID,
    teamId: TEAM_ID,
    resPath,
    refreshAssetCounts,
  }),
}));

vi.mock('../../../../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `/api/v1/resources/${id}/cover`,
  getResourceFileUrl: (id: string) => `/api/v1/resources/${id}/file`,
}));

// The real player builds a waveform off a media element; the sheet's question
// is only whether an audio asset gets one instead of a board.
vi.mock('../../../AudioWaveformPlayer', () => ({
  AudioWaveformPlayer: ({ src }: { src: string }) => <div data-testid="waveform" data-src={src} />,
}));

const fetchProjects = vi.fn();
vi.mock('../../../../services/projectsService', () => ({
  fetchProjects: (...a: unknown[]) => fetchProjects(...a),
}));

const createCanvas = vi.fn();
vi.mock('../../../../features/canvas-core/services/canvasService', () => ({
  createCanvas: (...a: unknown[]) => createCanvas(...a),
}));

const fetchAssetDetail = vi.fn();
const updateAsset = vi.fn();
const deleteAsset = vi.fn();
const duplicateAsset = vi.fn();
const createLink = vi.fn();
const deleteLink = vi.fn();
vi.mock('../../../../services/assetsService', () => ({
  fetchAssetDetail: (...a: unknown[]) => fetchAssetDetail(...a),
  updateAsset: (...a: unknown[]) => updateAsset(...a),
  deleteAsset: (...a: unknown[]) => deleteAsset(...a),
  duplicateAsset: (...a: unknown[]) => duplicateAsset(...a),
  createLink: (...a: unknown[]) => createLink(...a),
  deleteLink: (...a: unknown[]) => deleteLink(...a),
  listAssets: vi.fn().mockResolvedValue([]),
  createLoadout: vi.fn(),
  updateLoadout: vi.fn(),
  deleteLoadout: vi.fn(),
  translatePrompt: vi.fn(),
  regeneratePrompt: vi.fn(),
  ASSET_SOURCES: ['manual'],
  // `useAssetFailure` instance-checks this to tell a TYPED refusal (which
  // renders its own sentence) from anything else.
  GeneratedApiError: class GeneratedApiError extends Error {
    code = 'generic';
    extra: Record<string, unknown> = {};
  },
}));

import { AssetSheetPage } from './AssetSheetPage';
import {
  AUDIO_DETAIL,
  CHARACTER_DETAIL,
  COSTUME_DETAIL,
  PRESET_PROMPT_DETAIL,
  makeDetail,
} from './assetSheetFixtures';

/** Answer the sheet's own detail fetch with `main`, and every related-asset
 *  fetch out of `others` (a miss rejects, which is what an unreadable link
 *  looks like). */
function serveDetails(main: typeof CHARACTER_DETAIL, others: (typeof COSTUME_DETAIL)[] = []) {
  const byId = new Map(others.map((row) => [row.id, row]));
  fetchAssetDetail.mockImplementation(async (_scope: string, id: string) => {
    if (id === main.id) return main;
    const found = byId.get(id);
    if (found) return found;
    throw new Error(`no fixture for ${id}`);
  });
}

async function renderSheet(detail = CHARACTER_DETAIL, others = [COSTUME_DETAIL]) {
  serveDetails(detail, others);
  const utils = render(<AssetSheetPage assetId={detail.id} />);
  await waitFor(() => expect(screen.getByTestId('asset-sheet')).toBeTruthy());
  return utils;
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchProjects.mockResolvedValue([{ id: 55, name: 'Bamboo Sea' }]);
  updateAsset.mockResolvedValue({});
  deleteAsset.mockResolvedValue(undefined);
  duplicateAsset.mockResolvedValue({ ...CHARACTER_DETAIL, id: '999' });
  createLink.mockResolvedValue({});
  deleteLink.mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});

describe('skeleton', () => {
  it('renders breadcrumb, header, board, relations, prompt and sidebar', async () => {
    await renderSheet();
    expect(screen.getByTestId('crumb-assets')).toBeTruthy();
    // The type crumb reads `assets.types.<type>`, whose fallback is the raw
    // type name; the locale strings themselves are pinned by i18nParity.
    expect(screen.getByTestId('crumb-type')).toHaveTextContent('character');
    expect(screen.getByTestId('asset-sheet-header')).toHaveTextContent('Sang Yao');
    expect(screen.getByTestId('asset-board')).toBeTruthy();
    expect(
      screen.getAllByTestId('relation-section').map((el) => el.dataset.relationKey),
    ).toEqual(['wears', 'holds']);
    expect(screen.getByTestId('prompt-editor')).toBeTruthy();
    expect(screen.getByTestId('sheet-sidebar')).toBeTruthy();
  });

  it('shows the readiness chip from the server-derived field', async () => {
    await renderSheet();
    expect(screen.getByTestId('sheet-readiness')).toHaveAttribute('data-readiness', 'ready');

    const draft = makeDetail({
      ...CHARACTER_DETAIL,
      id: CHARACTER_DETAIL.id,
      readiness: { state: 'draft', missing: ['sheet'] },
    });
    await renderSheet(draft, [COSTUME_DETAIL]);
    const chips = screen.getAllByTestId('sheet-readiness');
    const last = chips[chips.length - 1];
    expect(last).toHaveAttribute('data-readiness', 'draft');
    // `missing` is RENDERED, not counted: "Draft" alone tells the user they
    // cannot use the asset without telling them what to do about it.
    expect(last).toHaveTextContent('sheet');
  });

  it('names linked assets by resolving them, and survives one it cannot read', async () => {
    await renderSheet(CHARACTER_DETAIL, []);
    // The costume fixture is absent, so its fetch rejects. The row must still
    // be there - a relation we could not name is still a relation.
    await waitFor(() => expect(screen.getByTestId('relation-row')).toBeTruthy());
    expect(screen.getByTestId('relation-row')).toHaveTextContent('727145299382534310');
  });

  it('a failed load is not a missing asset', async () => {
    fetchAssetDetail.mockRejectedValue(new Error('offline'));
    render(<AssetSheetPage assetId="1" />);
    await waitFor(() => expect(screen.getByTestId('sheet-load-error')).toBeTruthy());
    expect(screen.getByTestId('sheet-load-error')).toHaveTextContent('Could Not Load Assets');
  });
});

describe('per-type bodies', () => {
  it('a prompt asset has no board at all', async () => {
    await renderSheet(PRESET_PROMPT_DETAIL, []);
    expect(screen.queryByTestId('asset-board')).toBeNull();
    expect(screen.getByTestId('prompt-editor')).toBeTruthy();
    expect(screen.getByTestId('prompt-placeholders')).toBeTruthy();
    // Neither location nor prompt has relation sections.
    expect(screen.queryByTestId('relation-section')).toBeNull();
  });

  it('an audio asset gets a waveform and variants instead of a board', async () => {
    await renderSheet(AUDIO_DETAIL, []);
    expect(screen.queryByTestId('asset-board')).toBeNull();
    expect(screen.getByTestId('waveform')).toHaveAttribute(
      'data-src',
      '/api/v1/resources/727145299382534180/file',
    );
    expect(screen.getAllByTestId('audio-variant')).toHaveLength(2);
    expect(screen.getByTestId('audio-loopable')).toHaveTextContent('Loopable');
    expect(screen.getByTestId('audio-duration')).toHaveTextContent('1:32');
    expect(screen.getByTestId('relation-section')).toHaveAttribute('data-relation-key', 'attachedTo');
  });

  it('only characters get loadout chips', async () => {
    await renderSheet();
    expect(screen.getByTestId('loadout-chips')).toBeTruthy();
    await renderSheet(COSTUME_DETAIL, [CHARACTER_DETAIL]);
    expect(screen.queryAllByTestId('loadout-chips')).toHaveLength(1);
  });

  it('a costume shows the incoming side only', async () => {
    await renderSheet(COSTUME_DETAIL, [CHARACTER_DETAIL]);
    const section = screen.getByTestId('relation-section');
    expect(section).toHaveAttribute('data-relation-key', 'wornBy');
    expect(within(section).queryByTestId('relation-remove')).toBeNull();
  });
});

describe('inline editing', () => {
  it('a description edit PATCHes only description', async () => {
    await renderSheet();
    fireEvent.click(screen.getByTestId('sheet-description-edit'));
    fireEvent.change(screen.getByTestId('sheet-description-input'), {
      target: { value: 'Thirties, sun-scarred.' },
    });
    fireEvent.click(screen.getByTestId('sheet-description-save'));
    await waitFor(() => expect(updateAsset).toHaveBeenCalledTimes(1));
    expect(updateAsset).toHaveBeenCalledWith(SCOPE_ID, CHARACTER_DETAIL.id, {
      description: 'Thirties, sun-scarred.',
    });
    // A successful write re-reads: the row carries derived fields (readiness,
    // counts) the client cannot recompute.
    await waitFor(() => expect(fetchAssetDetail.mock.calls.length).toBeGreaterThan(1));
  });

  it('saving an unchanged description sends nothing', async () => {
    await renderSheet();
    fireEvent.click(screen.getByTestId('sheet-description-edit'));
    fireEvent.click(screen.getByTestId('sheet-description-save'));
    expect(updateAsset).not.toHaveBeenCalled();
  });

  it('an emptied name is a cancelled edit, not a 422', async () => {
    await renderSheet();
    fireEvent.click(screen.getByTestId('sheet-name-edit'));
    fireEvent.change(screen.getByTestId('sheet-name-input'), { target: { value: '  ' } });
    fireEvent.click(screen.getByTestId('sheet-name-save'));
    expect(updateAsset).not.toHaveBeenCalled();
    expect(screen.getByTestId('sheet-name')).toHaveTextContent('Sang Yao');
  });

  it('reports a refused PATCH', async () => {
    updateAsset.mockRejectedValueOnce(new Error('nope'));
    await renderSheet();
    fireEvent.click(screen.getByTestId('sheet-description-edit'));
    fireEvent.change(screen.getByTestId('sheet-description-input'), { target: { value: 'x' } });
    fireEvent.click(screen.getByTestId('sheet-description-save'));
    await waitFor(() => expect(addToast).toHaveBeenCalledWith(expect.any(String), 'error'));
  });
});

describe('relations', () => {
  it('removing a link calls the client with the id and the relation', async () => {
    await renderSheet();
    fireEvent.click(screen.getAllByTestId('relation-remove')[0]);
    await waitFor(() => expect(deleteLink).toHaveBeenCalled());
    expect(deleteLink).toHaveBeenCalledWith(
      SCOPE_ID,
      CHARACTER_DETAIL.id,
      '727145299382534310',
      'wears',
    );
  });
});

describe('the counts contract', () => {
  it('a delete refreshes the sidebar badges and leaves the sheet', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await renderSheet();
    fireEvent.click(screen.getByTestId('delete-asset'));
    await waitFor(() => expect(deleteAsset).toHaveBeenCalledWith(SCOPE_ID, CHARACTER_DETAIL.id));
    expect(refreshAssetCounts).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith('/team/42/resources/assets/character');
  });

  it('a declined confirm deletes nothing', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    await renderSheet();
    fireEvent.click(screen.getByTestId('delete-asset'));
    expect(deleteAsset).not.toHaveBeenCalled();
    expect(refreshAssetCounts).not.toHaveBeenCalled();
  });

  it('a duplicate refreshes the badges and opens the copy', async () => {
    await renderSheet();
    fireEvent.click(screen.getByTestId('duplicate-asset'));
    await waitFor(() => expect(duplicateAsset).toHaveBeenCalledWith(SCOPE_ID, CHARACTER_DETAIL.id));
    expect(refreshAssetCounts).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith('/team/42/resources/assets/item/999');
  });

  it('a failed delete does not touch the badges', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    deleteAsset.mockRejectedValueOnce(new Error('nope'));
    await renderSheet();
    fireEvent.click(screen.getByTestId('delete-asset'));
    await waitFor(() => expect(addToast).toHaveBeenCalledWith(expect.any(String), 'error'));
    expect(refreshAssetCounts).not.toHaveBeenCalled();
  });
});

describe('read-only presets', () => {
  it('offers no edit, no delete, and says why', async () => {
    await renderSheet(PRESET_PROMPT_DETAIL, []);
    expect(screen.getByTestId('asset-sheet')).toHaveAttribute('data-read-only', 'true');
    expect(screen.getByTestId('preset-badge')).toBeTruthy();
    expect(screen.getByTestId('preset-readonly-hint')).toBeTruthy();
    expect(screen.queryByTestId('sheet-name-edit')).toBeNull();
    expect(screen.queryByTestId('sheet-description-edit')).toBeNull();
    expect(screen.queryByTestId('delete-asset')).toBeNull();
    expect(screen.queryByTestId('relation-add')).toBeNull();
    // Duplicate is the way OUT of read-only, so it stays.
    expect(screen.getByTestId('duplicate-asset')).toBeTruthy();
  });
});

describe('Copy loadout prompt', () => {
  it('concatenates the asset, the loadout extra, then its costumes', async () => {
    const detail = makeDetail({
      ...CHARACTER_DETAIL,
      id: CHARACTER_DETAIL.id,
      loadouts: [
        {
          ...CHARACTER_DETAIL.loadouts[1],
          is_default: true,
        },
      ],
    });
    await renderSheet(detail, [COSTUME_DETAIL]);
    await waitFor(() => expect(screen.getByTestId('relation-row')).toHaveTextContent('Night Robe'));

    fireEvent.click(screen.getByTestId('copy-loadout-prompt'));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalled());
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      'same woman as reference…, black hooded night robe, black hooded robe, matte weave',
    );
    expect(addToast).toHaveBeenCalledWith('Prompt Copied', 'success');
  });

  it('says there is nothing to copy rather than copying an empty string', async () => {
    const bare = makeDetail({
      ...CHARACTER_DETAIL,
      id: CHARACTER_DETAIL.id,
      prompt_positive: null,
      loadouts: [],
      links: [],
    });
    await renderSheet(bare, []);
    fireEvent.click(screen.getByTestId('copy-loadout-prompt'));
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('There Is No Prompt To Copy Yet', 'info'),
    );
    expect(navigator.clipboard.writeText).not.toHaveBeenCalled();
  });
});

describe('Open in canvas', () => {
  it('uses the asset own project when it has exactly one', async () => {
    createCanvas.mockResolvedValue({ id: 'c1' });
    await renderSheet();
    fireEvent.click(screen.getByTestId('open-in-canvas'));
    await waitFor(() => expect(createCanvas).toHaveBeenCalled());
    expect(createCanvas).toHaveBeenCalledWith('55', {
      name: 'Sang Yao',
      kind: 'character',
      asset_id: CHARACTER_DETAIL.id,
    });
    expect(navigate).toHaveBeenCalledWith('/team/42/canvas/c1');
  });

  it('asks which project when the asset is linked to none', async () => {
    createCanvas.mockResolvedValue({ id: 'c2' });
    const orphan = makeDetail({ ...CHARACTER_DETAIL, id: CHARACTER_DETAIL.id, project_ids: [] });
    await renderSheet(orphan, [COSTUME_DETAIL]);
    fireEvent.click(screen.getByTestId('open-in-canvas'));
    await waitFor(() => expect(screen.getByTestId('canvas-project-picker')).toBeTruthy());
    // A canvas belongs to a project; picking one silently would put it
    // somewhere the user never chose.
    expect(createCanvas).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId('canvas-project-option'));
    await waitFor(() => expect(createCanvas).toHaveBeenCalledWith('55', expect.anything()));
  });

  it('a costume falls back to a smart canvas', async () => {
    createCanvas.mockResolvedValue({ id: 'c3' });
    const costume = makeDetail({ ...COSTUME_DETAIL, id: COSTUME_DETAIL.id, project_ids: ['55'] });
    await renderSheet(costume, [CHARACTER_DETAIL]);
    fireEvent.click(screen.getByTestId('open-in-canvas'));
    await waitFor(() => expect(createCanvas).toHaveBeenCalled());
    expect(createCanvas).toHaveBeenCalledWith('55', expect.objectContaining({ kind: 'smart' }));
  });
});

describe('the deferred surfaces', () => {
  it('Send To Canvas and Send To Agent are disabled with a reason', async () => {
    await renderSheet();
    const canvas = screen.getByTestId('send-to-canvas');
    const agent = screen.getByTestId('send-to-agent');
    expect(canvas).toBeDisabled();
    expect(canvas).toHaveAttribute('title', 'Arrives with P4');
    expect(agent).toBeDisabled();
    expect(agent).toHaveAttribute('title', 'Arrives with P5');
  });

  it('Used In lists the projects by name and states the canvas gap', async () => {
    await renderSheet();
    await waitFor(() =>
      expect(screen.getByTestId('used-in-project')).toHaveTextContent('Bamboo Sea'),
    );
    expect(screen.getByTestId('used-in-panel')).toHaveTextContent(
      'Canvas usage arrives with P4',
    );
  });

  it('renders no generation history panel', async () => {
    // `GET /generated` has no `source_asset_id` filter, so the only honest
    // options were "nothing" or "the whole inbox under this asset's name".
    await renderSheet();
    expect(screen.queryByTestId('generation-history')).toBeNull();
  });
});
