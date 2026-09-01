/**
 * LinkFromLibraryDialog — the search that must not offer a click with no
 * visible effect.
 *
 * `searchAssets` answers over the whole scope, so it returns rows the panel is
 * already showing AND global presets. Both are unlinkable-in-practice for
 * different reasons — a duplicate ref is `on_conflict_do_nothing` (a 201 that
 * changes nothing), a preset has a NULL scope and is refused with
 * `project_scope_mismatch` — so both are filtered before render, and the two
 * empty outcomes ("nothing matched" vs "everything matched is already here")
 * say which one happened.
 *
 * Fixtures are the real `AssetSummary` wire shape: string ids, `scope_id` null
 * only on the preset.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const translate = (key: string, opts?: string | Record<string, unknown>): string => {
  const bundle: Record<string, string> = {
    'assets.project.linkTitle': 'Link {{type}} From Library',
    'assets.project.search': 'Search The Library',
    'assets.project.noCandidates': 'Nothing In The Library Matches',
    'assets.project.allLinked': 'Everything Found Is Already In This Project',
    'assets.project.searchFailed': 'Could Not Search The Library',
    'assets.err.not_a_member': 'You are not a member of this workspace.',
    'assets.err.generic': 'Something went wrong. Please try again.',
    'assets.readiness.ready': 'Ready',
    'assets.readiness.draft': 'Draft',
    'saveAsAsset.type.character': 'Character',
    'common.cancel': 'Cancel',
    'common.loading': 'Loading…',
  };
  const fallback = typeof opts === 'string' ? opts : (opts?.defaultValue as string | undefined);
  let out = bundle[key] ?? fallback ?? key;
  if (opts && typeof opts === 'object') {
    for (const [name, value] of Object.entries(opts)) {
      if (name === 'defaultValue') continue;
      out = out.split(`{{${name}}}`).join(String(value));
    }
  }
  return out;
};

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: string | Record<string, unknown>) => translate(k, o),
  }),
}));

const addToast = vi.fn();
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));

const searchAssets = vi.fn();
const linkProject = vi.fn();
vi.mock('../../services/assetsService', async () => {
  const { GeneratedApiError } = await import('../../services/apiEnvelope');
  return {
    searchAssets: (...a: unknown[]) => searchAssets(...a),
    linkProject: (...a: unknown[]) => linkProject(...a),
    GeneratedApiError,
  };
});

import { GeneratedApiError } from '../../services/apiEnvelope';
import { LinkFromLibraryDialog } from './LinkFromLibraryDialog';
import type { AssetSummary } from '../../services/assetsService';

const SCOPE = '727145299382534200';
const PROJECT = '727145299382534055';

function summary(over: Partial<AssetSummary> = {}): AssetSummary {
  return {
    id: '727145299382534300',
    scope_id: SCOPE,
    asset_type: 'character',
    name: 'Sang Yao',
    role_tag: 'lead',
    readiness: { state: 'ready', missing: [] },
    cover_file_id: null,
    is_system_preset: false,
    ...over,
  };
}

const onLinked = vi.fn();
const onClose = vi.fn();

function mount(linkedIds: string[] = []) {
  return render(
    <LinkFromLibraryDialog
      open
      scopeId={SCOPE}
      projectId={PROJECT}
      assetType="character"
      linkedIds={linkedIds}
      onClose={onClose}
      onLinked={onLinked}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  searchAssets.mockResolvedValue([]);
  linkProject.mockResolvedValue(undefined);
});

describe('LinkFromLibraryDialog', () => {
  it('searches the scope for the panel’s type', async () => {
    mount();
    await waitFor(() =>
      expect(searchAssets).toHaveBeenCalledWith(SCOPE, {
        type: 'character',
        q: undefined,
        limit: 40,
      }),
    );
  });

  it('offers only rows the project does not already have', async () => {
    searchAssets.mockResolvedValue([
      summary(),
      summary({ id: '727145299382534301', name: 'Lin Mo' }),
    ]);
    mount(['727145299382534300']);

    await waitFor(() => expect(screen.getAllByTestId('link-candidate')).toHaveLength(1));
    expect(screen.getByTestId('link-candidate').getAttribute('data-asset-id')).toBe(
      '727145299382534301',
    );
  });

  it('excludes system presets — their NULL scope makes the link a guaranteed 422', async () => {
    searchAssets.mockResolvedValue([
      summary({ id: '727145299382534303', name: 'Preset Face', scope_id: null, is_system_preset: true }),
    ]);
    mount();

    await waitFor(() => expect(screen.getByTestId('link-no-candidates')).toBeTruthy());
    expect(screen.queryAllByTestId('link-candidate')).toHaveLength(0);
  });

  it('distinguishes "nothing matched" from "all of it is already here"', async () => {
    searchAssets.mockResolvedValue([summary()]);
    mount(['727145299382534300']);

    await waitFor(() =>
      expect(screen.getByTestId('link-no-candidates').textContent).toBe(
        'Everything Found Is Already In This Project',
      ),
    );
  });

  it('says nothing matched when the search really came back empty', async () => {
    mount();
    await waitFor(() =>
      expect(screen.getByTestId('link-no-candidates').textContent).toBe(
        'Nothing In The Library Matches',
      ),
    );
  });

  it('a failed search is not an empty library', async () => {
    searchAssets.mockRejectedValue(new GeneratedApiError(403, 'not_a_member', 'Nope'));
    mount();

    const alert = await screen.findByTestId('link-search-error');
    expect(alert.getAttribute('role')).toBe('alert');
    expect(screen.queryByTestId('link-no-candidates')).toBeNull();
    expect(addToast).toHaveBeenCalled();
  });

  it('links the picked asset, tells the panel to refetch, and closes', async () => {
    searchAssets.mockResolvedValue([summary()]);
    mount();

    await waitFor(() => expect(screen.getByTestId('link-candidate')).toBeTruthy());
    fireEvent.click(screen.getByTestId('link-candidate'));

    await waitFor(() =>
      expect(linkProject).toHaveBeenCalledWith(SCOPE, '727145299382534300', PROJECT),
    );
    expect(onLinked).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('a refused link reports itself and leaves the dialog open', async () => {
    searchAssets.mockResolvedValue([summary()]);
    linkProject.mockRejectedValue(
      new GeneratedApiError(422, 'project_scope_mismatch', 'Different team'),
    );
    mount();

    await waitFor(() => expect(screen.getByTestId('link-candidate')).toBeTruthy());
    fireEvent.click(screen.getByTestId('link-candidate'));

    await waitFor(() => expect(addToast).toHaveBeenCalled());
    expect(onLinked).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('passes the typed query through', async () => {
    mount();
    await waitFor(() => expect(searchAssets).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText('Search The Library'), { target: { value: 'sang' } });

    await waitFor(() =>
      expect(searchAssets).toHaveBeenLastCalledWith(SCOPE, {
        type: 'character',
        q: 'sang',
        limit: 40,
      }),
    );
  });
});
