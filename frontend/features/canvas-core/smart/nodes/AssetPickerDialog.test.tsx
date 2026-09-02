// features/canvas-core/smart/nodes/AssetPickerDialog.test.tsx
//
// The library picker behind both canvas entry points (P4 Task 4).
//
// The pin that matters: a pick fetches the DETAIL row before creating
// anything. `searchAssets` answers `AssetSummary`, which has no `files` —
// handing that straight to `createAssetNode` would place a card referencing
// nothing while looking like it worked.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { GeneratedApiError } from '../../../../services/apiEnvelope';
import { AssetPickerDialog } from './AssetPickerDialog';

const searchAssets = vi.fn();
const fetchAssetDetail = vi.fn();

vi.mock('../../../../services/assetsService', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    searchAssets: (...a: unknown[]) => searchAssets(...a),
    fetchAssetDetail: (...a: unknown[]) => fetchAssetDetail(...a),
  };
});

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown) =>
      typeof fallback === 'string' ? fallback : key,
  }),
}));

const SCOPE = '727145299382534100';
const ASSET_ID = '727145299382534300';

const SUMMARY = {
  id: ASSET_ID,
  scope_id: SCOPE,
  asset_type: 'character' as const,
  name: 'Cole Bannon',
  role_tag: 'lead',
  readiness: { state: 'ready' as const, missing: [] },
  cover_file_id: null,
  is_system_preset: false,
};

const DETAIL = { ...SUMMARY, files: [], links: [], linked_by: [], loadouts: [] };

function renderPicker(
  onPick = vi.fn(),
  onClose = vi.fn(),
  teamId: string | null = SCOPE,
) {
  const path = teamId ? `/team/${teamId}/canvas/9` : '/canvas/9';
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path={teamId ? '/team/:teamId/canvas/:canvasId' : '/canvas/:canvasId'}
          element={<AssetPickerDialog onPick={onPick} onClose={onClose} />}
        />
      </Routes>
    </MemoryRouter>,
  );
  return { onPick, onClose };
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  searchAssets.mockReset();
  fetchAssetDetail.mockReset();
  searchAssets.mockResolvedValue([SUMMARY]);
  fetchAssetDetail.mockResolvedValue(DETAIL);
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('AssetPickerDialog', () => {
  it('searches the URL team scope and lists what comes back', async () => {
    renderPicker();
    await waitFor(() =>
      expect(searchAssets).toHaveBeenCalledWith(SCOPE, {
        q: undefined,
        type: undefined,
        limit: 60,
      }),
    );
    expect(await screen.findByText('Cole Bannon')).toBeTruthy();
  });

  it('a type chip narrows the query', async () => {
    renderPicker();
    await waitFor(() => expect(searchAssets).toHaveBeenCalled());
    // The i18n mock answers with the raw fallback; the real locale renders
    // "Prop" from `saveAsAsset.type.prop`.
    fireEvent.click(screen.getByRole('button', { name: 'prop' }));
    await waitFor(() =>
      expect(searchAssets).toHaveBeenLastCalledWith(SCOPE, {
        q: undefined,
        type: 'prop',
        limit: 60,
      }),
    );
  });

  it('picking fetches the DETAIL row and hands THAT to the caller', async () => {
    const { onPick, onClose } = renderPicker();
    const row = await screen.findByTestId('asset-picker-row');
    fireEvent.click(row);
    await waitFor(() => expect(onPick).toHaveBeenCalledTimes(1));
    expect(fetchAssetDetail).toHaveBeenCalledWith(SCOPE, ASSET_ID);
    // The detail row, not the summary — `files` is what seeds the node.
    expect(onPick.mock.calls[0][0]).toHaveProperty('files');
    expect(onClose).toHaveBeenCalled();
  });

  it('a failed detail fetch places nothing and says so', async () => {
    fetchAssetDetail.mockRejectedValue(
      new GeneratedApiError(500, 'http_500', 'boom'),
    );
    const { onPick, onClose } = renderPicker();
    fireEvent.click(await screen.findByTestId('asset-picker-row'));
    await waitFor(() =>
      expect(screen.getByTestId('asset-picker-pick-error')).toBeTruthy(),
    );
    // Silently placing an unseeded card would look exactly like success.
    expect(onPick).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('a failed search reports rather than rendering as an empty library', async () => {
    searchAssets.mockRejectedValue(new GeneratedApiError(503, 'http_503', 'down'));
    renderPicker();
    await waitFor(() => expect(screen.getByTestId('asset-picker-error')).toBeTruthy());
    expect(screen.queryByTestId('asset-picker-empty')).toBeNull();
  });

  it('no team segment means no scope — it asks nothing and says so', async () => {
    renderPicker(vi.fn(), vi.fn(), null);
    await waitFor(() => expect(screen.getByTestId('asset-picker-error')).toBeTruthy());
    expect(searchAssets).not.toHaveBeenCalled();
  });

  it('an empty result is distinguishable from a failure', async () => {
    searchAssets.mockResolvedValue([]);
    renderPicker();
    expect(await screen.findByTestId('asset-picker-empty')).toBeTruthy();
    expect(screen.queryByTestId('asset-picker-error')).toBeNull();
  });

  it('Escape closes without picking', async () => {
    const { onPick, onClose } = renderPicker();
    await waitFor(() => expect(searchAssets).toHaveBeenCalled());
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
    expect(onPick).not.toHaveBeenCalled();
  });
});
